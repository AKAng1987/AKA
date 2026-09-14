"""
markov_phase2_train.py -- Train the Phase 2 experimental divergence
classifiers (credit_up, inflation_up) on full history and publish the
artifact to S3 for cmon-stage-backend-regime-divergence-updater.

Model is StandardScaler + LogisticRegression, exported as plain JSON
(scaler mean/scale, coefficients, intercept). The Lambda scores with a
numpy dot product -- no sklearn, no pickle, no cross-version coupling.
Gradient boosting scored a few points higher out-of-sample in the M4
prototype but the gap is within noise at ~50 effective samples, and it
would have needed scipy+sklearn in the Lambda (over the 250MB layer cap
with pandas). See MARKOV_PHASE2_PLAN.md s8.

Reads price-history + model-history from DynamoDB via the prototype's
loaders; features come from the SAME module the Lambda imports
(lambda/regime_divergence_updater/markov_features.py).

Usage:
  DRY_RUN=True  .venv/bin/python scripts/markov_phase2_train.py   # train, report, save local only
  DRY_RUN=False .venv/bin/python scripts/markov_phase2_train.py   # also upload to S3

Retrain quarterly (or whenever model-history gains a few transitions);
bump MODEL_VERSION when features or hyperparameters change.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "lambda", "regime_divergence_updater"))
import markov_features as mf  # noqa: E402
import markov_phase2_prototype as proto  # noqa: E402  -- load_close, load_ground_truth, daily_quadrant

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
MODEL_VERSION = os.environ.get("MODEL_VERSION", "v1")
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
S3_KEY = f"Cache/markov/models/divergence_{MODEL_VERSION}.json"
LOCAL_OUT = os.path.join(proto.OUT_DIR, f"divergence_{MODEL_VERSION}.json")
FIRST_TEST_YEAR = 2011
PURGE_DAYS = 60
RANDOM_STATE = 42

# (axis_key, model_history name, which element of Q_TO_AXES)
TARGETS = [
    ("credit", "compass_US", 1),
    ("inflation", "grid_US", 1),
]


def make_model():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=2000, C=0.5, random_state=RANDOM_STATE),
    )


def export_logistic(pipe) -> dict:
    """Plain-JSON form the Lambda can score with numpy alone."""
    scaler, lr = pipe.named_steps["standardscaler"], pipe.named_steps["logisticregression"]
    assert list(lr.classes_) == [0, 1], lr.classes_
    return {
        "type": "logistic",
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": lr.coef_[0].tolist(),
        "intercept": float(lr.intercept_[0]),
    }


def walk_forward_summary(X: pd.DataFrame, y: pd.Series) -> dict:
    preds, truths, bases = [], [], []
    for yr in sorted(set(X.index.year)):
        if yr < FIRST_TEST_YEAR:
            continue
        te = X.index.year == yr
        tr = X.index < (X.index[te][0] - pd.Timedelta(days=PURGE_DAYS))
        if tr.sum() < 500 or len(set(y[te])) > len(set(y[tr])):
            continue
        m = make_model().fit(X[tr].values, y[tr].values)
        preds.append(m.predict(X[te].values))
        truths.append(y[te].values)
        bases.append(np.full(te.sum(), y[tr].value_counts().idxmax()))
    p, t, b = map(np.concatenate, (preds, truths, bases))
    return {
        "oos_accuracy": float((p == t).mean()),
        "oos_base_rate": float((b == t).mean()),
        "oos_lift": float((p == t).mean() - (b == t).mean()),
        "oos_days": int(len(t)),
        "oos_range": [str(X.index[X.index.year >= FIRST_TEST_YEAR][0].date()), str(X.index[-1].date())],
    }


def main() -> None:
    print(f"=== markov_phase2_train.py  {MODEL_VERSION}  {'DRY RUN' if DRY_RUN else 'LIVE (upload to S3)'} ===")
    print("Building features from DynamoDB (full history)...")
    feats = mf.build_features(proto.load_close)
    print(f"  {feats.shape[0]} days x {feats.shape[1]} features, {feats.index[0].date()} -> {feats.index[-1].date()}")

    artifact = {
        "model_version": MODEL_VERSION,
        "feature_version": mf.FEATURE_VERSION,
        "feature_names": mf.FEATURE_NAMES,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sklearn_version": __import__("sklearn").__version__,
        "numpy_version": np.__version__,
        "models": {},
        "meta": {},
    }

    for axis_key, model_name, idx in TARGETS:
        ev = proto.load_ground_truth(model_name)
        X = feats.loc[feats.index >= ev.index[0]]
        yq = proto.daily_quadrant(ev, X.index).dropna().astype(int)
        X = X.loc[yq.index]
        y = yq.map(lambda q: mf.Q_TO_AXES[q][idx])

        print(f"\n[{axis_key}] target from {model_name}, {len(X)} days, {X.index[0].date()} -> {X.index[-1].date()}")
        summ = walk_forward_summary(X, y)
        print(f"  walk-forward OOS: acc {summ['oos_accuracy']:.1%}  base {summ['oos_base_rate']:.1%}  lift {summ['oos_lift']:+.1%}  ({summ['oos_days']} days)")

        final = make_model().fit(X.values, y.values)
        artifact["models"][axis_key] = export_logistic(final)
        # Self-check: numpy scoring must reproduce sklearn's predict_proba.
        ex = artifact["models"][axis_key]
        z = (X.values - np.array(ex["scaler_mean"])) / np.array(ex["scaler_scale"])
        p_np = 1.0 / (1.0 + np.exp(-(z @ np.array(ex["coef"]) + ex["intercept"])))
        p_sk = final.predict_proba(X.values)[:, 1]
        max_diff = float(np.abs(p_np - p_sk).max())
        assert max_diff < 1e-9, f"numpy scoring diverges from sklearn: {max_diff}"
        print(f"  numpy-vs-sklearn scoring check: max_diff={max_diff:.2e}  PASS")
        artifact["meta"][axis_key] = {
            **summ,
            "source_model": model_name,
            "train_range": [str(X.index[0].date()), str(X.index[-1].date())],
            "train_days": int(len(X)),
            "n_transitions": int(len(ev)),
            "class_balance_up": float(y.mean()),
        }

    os.makedirs(os.path.dirname(LOCAL_OUT), exist_ok=True)
    body = json.dumps(artifact, indent=1)
    with open(LOCAL_OUT, "w") as fh:
        fh.write(body)
    print(f"\nSaved local artifact: {LOCAL_OUT}  ({len(body) / 1024:.0f} KB)")
    print("Meta:")
    print(json.dumps(artifact["meta"], indent=2))

    if DRY_RUN:
        print("\nDRY RUN -- not uploaded. Set DRY_RUN=False to push to S3.")
        return
    s3 = boto3.client("s3", region_name="ap-southeast-1")
    s3.put_object(
        Bucket=BUCKET, Key=S3_KEY, Body=body.encode("utf-8"), ContentType="application/json",
        Metadata={"model_version": MODEL_VERSION, "feature_version": str(mf.FEATURE_VERSION)},
    )
    print(f"Uploaded: s3://{BUCKET}/{S3_KEY}")


if __name__ == "__main__":
    main()

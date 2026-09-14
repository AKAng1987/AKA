"""
markov_phase2_classifier.py -- Supervised market-implied regime read for
Markov Phase 2, replacing the unsupervised HMM approach that
markov_phase2_prototype.py showed does not recover the Tesseract quadrants
(agreement at majority-class base rate on 18-22 years of data).

Read-only. No AWS writes.

Idea: we have ~5,500 labeled days per axis (model-history quadrant,
forward-filled daily). Train a classifier on market-observable features ->
quadrant, output P(quadrant | today's market). That IS the "market-implied
regime". divergence_score = 1 - P(discrete quadrant), exactly as
MARKOV_PHASE2_PLAN.md s2 defines it; divergence_direction = argmax quadrant
when it differs from the discrete one.

Validation is walk-forward (expanding window, yearly test folds, 60-day
purge gap between train end and test start so a regime spell straddling
the boundary can't leak). Every number reported is out-of-sample.

Features (richer than the HMM run -- adds proxies for the axes it was
blind to):
  Liquidity : vix_level/1d/5d, t10y2y_level/1d/20d
  Credit    : credit_spread (BAA10Y) level/1d/20d, kre_rs (KRE/SPY 50d RS)
  Growth    : xly_xlp_20d (cyclicals vs defensives), copper_20d,
              sector_rs_dispersion, breadth_proxy
  Inflation : t10yie_level/1d/20d (10Y TIPS breakeven), dbc_20d
Deliberately EXCLUDED: DFF / DFEDTARU (the policy rate is the Liquidity
print itself, not a market read).

Usage:
  .venv/bin/python scripts/markov_phase2_classifier.py
  REBUILD_FEATURES=1 ...   # refetch from DDB/FRED instead of cache
  MODEL=logreg ...         # baseline instead of gradient boosting
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import markov_phase2_prototype as proto  # noqa: E402  -- loaders + daily_quadrant

warnings.filterwarnings("ignore")

OUT_DIR = proto.OUT_DIR
MODEL = os.environ.get("MODEL", "hgb")  # "hgb" | "logreg"
TARGET = os.environ.get("TARGET", "quadrant")  # "quadrant" | "axes"

# Quadrant -> (first_axis_up, second_axis_up). Same arrow table for both
# axes (COMPASS_Q_MAP == GRID_Q_MAP in web/lib/regimeConstants.ts):
#   Q1 (up, down)  Q2 (up, up)  Q3 (down, up)  Q4 (down, down)
# Compass: (Liquidity, Credit). Grid: (Growth, Inflation).
Q_TO_AXES = {1: (1, 0), 2: (1, 1), 3: (0, 1), 4: (0, 0)}
AXES_TO_Q = {v: k for k, v in Q_TO_AXES.items()}
AXIS_NAMES = {"compass": ("liquidity_up", "credit_up"), "grid": ("growth_up", "inflation_up")}
FIRST_TEST_YEAR = int(os.environ.get("FIRST_TEST_YEAR", "2011"))
PURGE_DAYS = 60
RANDOM_STATE = 42


# ── Features ─────────────────────────────────────────────────────────────────

def rs50(num: pd.Series, den: pd.Series) -> pd.Series:
    raw = (num / den).dropna()
    return (100.0 * raw / raw.rolling(50, min_periods=20).mean()).dropna()


def build_features() -> pd.DataFrame:
    print("Loading base series (VIX, T10Y2Y, SPY, sectors)...")
    vix = proto.load_close("VIX")
    t10y2y = proto.load_close("T10Y2Y")
    spy = proto.load_close("SPY")
    sec = {s: proto.load_close(s) for s in proto.SECTORS}

    print("Loading richer-feature series (KRE, COPPER, DBC, FRED BAA10Y/T10YIE)...")
    kre = proto.load_close("KRE")
    copper = proto.load_close("COPPER")
    dbc = proto.load_close("DBC")
    credit = proto.load_fred("BAA10Y")
    t10yie = proto.load_fred("T10YIE")

    rs = pd.DataFrame({s: rs50(c, spy) for s, c in sec.items()})
    rs_disp = rs.std(axis=1, skipna=True)
    rs_disp[rs.notna().sum(axis=1) < 5] = np.nan
    above = pd.DataFrame({
        s: (c > c.rolling(20, min_periods=10).mean()).astype(float).where(c.notna())
        for s, c in sec.items()
    })
    breadth = above.mean(axis=1, skipna=True)
    breadth[above.notna().sum(axis=1) < 5] = np.nan

    xly_xlp = (sec["XLY"] / sec["XLP"]).dropna()

    f = pd.DataFrame({
        # Liquidity
        "vix_level": vix,
        "vix_1d": vix.diff(),
        "vix_5d": vix.diff(5),
        "t10y2y_level": t10y2y,
        "t10y2y_1d": t10y2y.diff(),
        "t10y2y_20d": t10y2y.diff(20),
        # Credit
        "credit_level": credit,
        "credit_1d": credit.diff(),
        "credit_20d": credit.diff(20),
        "kre_rs": rs50(kre, spy),
        # Growth
        "xly_xlp_20d": xly_xlp.pct_change(20),
        "copper_20d": copper.pct_change(20),
        "sector_rs_dispersion": rs_disp,
        "breadth_proxy": breadth,
        # Inflation
        "t10yie_level": t10yie,
        "t10yie_1d": t10yie.diff(),
        "t10yie_20d": t10yie.diff(20),
        "dbc_20d": dbc.pct_change(20),
    })
    return f.replace([np.inf, -np.inf], np.nan).ffill(limit=5).dropna()


# ── Walk-forward ─────────────────────────────────────────────────────────────

def make_model():
    if MODEL == "logreg":
        return make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=2000, C=0.5, random_state=RANDOM_STATE),
        )
    return HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=40,
        l2_regularization=1.0,
        random_state=RANDOM_STATE,
    )


def walk_forward(X: pd.DataFrame, y: pd.Series, axis: str) -> pd.DataFrame:
    """Expanding-window yearly folds with a purge gap. Returns OOS frame:
    truth, pred, p_Q1..p_Q4, base_pred (train-majority class)."""
    years = sorted(set(X.index.year))
    test_years = [yr for yr in years if yr >= FIRST_TEST_YEAR]
    out = []
    print(f"\n[{axis}] walk-forward: {len(test_years)} folds ({test_years[0]}-{test_years[-1]}), purge={PURGE_DAYS}d, model={MODEL}")
    print(f"  {'year':>4} {'n_train':>7} {'n_test':>6} {'acc':>6} {'base':>6} {'lift':>6} {'logloss':>8}")
    for yr in test_years:
        test_mask = X.index.year == yr
        test_start = X.index[test_mask][0]
        train_end = test_start - pd.Timedelta(days=PURGE_DAYS)
        train_mask = X.index < train_end
        if train_mask.sum() < 500:
            continue
        Xtr, ytr = X[train_mask], y[train_mask]
        Xte, yte = X[test_mask], y[test_mask]
        # Skip fold if training set lacks a class the test set has -- can't
        # score a class the model has never seen.
        if not set(yte.unique()).issubset(set(ytr.unique())):
            missing = set(yte.unique()) - set(ytr.unique())
            print(f"  {yr:>4} skipped: test has Q{sorted(missing)} never seen in train")
            continue
        m = make_model().fit(Xtr.values, ytr.values)
        proba = m.predict_proba(Xte.values)
        classes = list(m.classes_)
        pred = np.array(classes)[proba.argmax(axis=1)]
        base = ytr.value_counts().idxmax()
        acc = float((pred == yte.values).mean())
        base_acc = float((yte.values == base).mean())
        ll = log_loss(yte.values, proba, labels=classes)
        print(f"  {yr:>4} {len(Xtr):>7} {len(Xte):>6} {acc:>6.1%} {base_acc:>6.1%} {acc - base_acc:>+6.1%} {ll:>8.3f}")
        df = pd.DataFrame({"truth": yte.values, "pred": pred, "base_pred": base}, index=Xte.index)
        for c in sorted(set(y.unique())):
            df[f"p_{c}"] = proba[:, classes.index(c)] if c in classes else 0.0
        out.append(df)
    return pd.concat(out)


def report_binary(oos: pd.DataFrame, name: str) -> None:
    acc = float((oos["pred"] == oos["truth"]).mean())
    base = float((oos["base_pred"] == oos["truth"]).mean())
    t1 = oos["truth"] == 1
    rec1 = float((oos.loc[t1, "pred"] == 1).mean()) if t1.sum() else float("nan")
    rec0 = float((oos.loc[~t1, "pred"] == 0).mean()) if (~t1).sum() else float("nan")
    print(f"\n[{name}] POOLED OOS: accuracy {acc:.1%}  base {base:.1%}  lift {acc - base:+.1%}   recall(up) {rec1:.1%}  recall(down) {rec0:.1%}")


def report(oos: pd.DataFrame, axis: str, feat_names: list[str], X: pd.DataFrame, y: pd.Series) -> None:
    acc = float((oos["pred"] == oos["truth"]).mean())
    base = float((oos["base_pred"] == oos["truth"]).mean())
    print(f"\n[{axis}] POOLED OUT-OF-SAMPLE  ({oos.index[0].date()} -> {oos.index[-1].date()}, {len(oos)} days)")
    print(f"  accuracy {acc:.1%}   base rate {base:.1%}   lift {acc - base:+.1%}")
    print("  per-quadrant recall / precision (OOS):")
    for q in (1, 2, 3, 4):
        t = oos["truth"] == q
        p = oos["pred"] == q
        rec = float((oos.loc[t, "pred"] == q).mean()) if t.sum() else float("nan")
        prec = float((oos.loc[p, "truth"] == q).mean()) if p.sum() else float("nan")
        print(f"    Q{q}: recall {rec:6.1%}  precision {prec:6.1%}  ({int(t.sum())} days)")

    # Divergence as spec'd: 1 - P(discrete quadrant)
    p_truth = np.array([oos.iloc[i][f"p_{int(oos.iloc[i]['truth'])}"] for i in range(len(oos))])
    div = pd.Series(1.0 - p_truth, index=oos.index)
    print(f"  divergence_score: mean {div.mean():.3f}  median {div.median():.3f}  p90 {div.quantile(0.9):.3f}")

    truth_s = oos["truth"]
    trans = truth_s.index[truth_s.ne(truth_s.shift()).values][1:]
    pre, post = [], []
    for d in trans:
        loc = oos.index.get_loc(d)
        if loc >= 20:
            pre.append(div.iloc[loc - 20:loc].mean())
        if loc + 20 < len(oos):
            post.append(div.iloc[loc:loc + 20].mean())
    if pre:
        print(f"  transitions in OOS window: {len(trans)}")
        print(f"  mean divergence 20d BEFORE transition: {np.mean(pre):.3f}  vs overall {div.mean():.3f}  -> {'LEADS' if np.mean(pre) > div.mean() * 1.15 else 'no clear lead'}")
        print(f"  mean divergence 20d AFTER  transition: {np.mean(post):.3f}  (classifier catching up to the new print)")

    # Feature importance from a final fit on everything (diagnostic only)
    if MODEL == "hgb":
        from sklearn.inspection import permutation_importance
        m = make_model().fit(X.values, y.values)
        imp = permutation_importance(m, X.values, y.values, n_repeats=3, random_state=RANDOM_STATE, n_jobs=-1)
        order = np.argsort(-imp.importances_mean)[:8]
        print("  top features (permutation importance, in-sample diagnostic):")
        for i in order:
            print(f"    {feat_names[i]:22s} {imp.importances_mean[i]:+.4f}")

    out = oos.copy()
    out["divergence"] = div
    path = os.path.join(OUT_DIR, f"{axis}_classifier_{MODEL}_oos.parquet")
    out.to_parquet(path)
    print(f"  saved: {path}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"=== Markov Phase 2 supervised classifier  (model={MODEL}) ===")
    cache = os.path.join(OUT_DIR, "features_classifier.parquet")
    if os.path.exists(cache) and os.environ.get("REBUILD_FEATURES") != "1":
        print(f"Loading cached features: {cache}")
        feats = pd.read_parquet(cache)
    else:
        feats = build_features()
        feats.to_parquet(cache)
        print(f"Cached: {cache}")
    print(f"Feature matrix: {feats.shape[0]} days x {feats.shape[1]} features, {feats.index[0].date()} -> {feats.index[-1].date()}")

    truth_events = {m: proto.load_ground_truth(m) for m in ("compass_US", "grid_US")}
    for axis, model_name in (("compass", "compass_US"), ("grid", "grid_US")):
        ev = truth_events[model_name]
        X = feats.loc[feats.index >= ev.index[0]]
        y = proto.daily_quadrant(ev, X.index).dropna().astype(int)
        X = X.loc[y.index]

        if TARGET == "quadrant":
            oos = walk_forward(X, y, axis)
            report(oos, axis, list(X.columns), X, y)
            continue

        # axes mode: two binary problems, then recombine into a quadrant.
        a_name, b_name = AXIS_NAMES[axis]
        y_a = y.map(lambda q: Q_TO_AXES[q][0])
        y_b = y.map(lambda q: Q_TO_AXES[q][1])
        oos_a = walk_forward(X, y_a, f"{axis}/{a_name}")
        report_binary(oos_a, f"{axis}/{a_name}")
        oos_b = walk_forward(X, y_b, f"{axis}/{b_name}")
        report_binary(oos_b, f"{axis}/{b_name}")

        both = oos_a.join(oos_b, lsuffix="_a", rsuffix="_b", how="inner")
        q_pred = both.apply(lambda r: AXES_TO_Q[(int(r["pred_a"]), int(r["pred_b"]))], axis=1)
        q_truth = both.apply(lambda r: AXES_TO_Q[(int(r["truth_a"]), int(r["truth_b"]))], axis=1)
        q_base = y.loc[both.index].value_counts().idxmax()
        acc = float((q_pred == q_truth).mean())
        base = float((q_truth == q_base).mean())
        print(f"\n[{axis}] RECOMBINED QUADRANT (OOS): accuracy {acc:.1%}  base {base:.1%}  lift {acc - base:+.1%}")
        for q in (1, 2, 3, 4):
            t = q_truth == q
            rec = float((q_pred[t] == q).mean()) if t.sum() else float("nan")
            print(f"    Q{q}: recall {rec:6.1%}  ({int(t.sum())} days)")
        # Divergence from the product of axis probabilities.
        p_q_truth = np.array([
            (both.iloc[i]["p_1_a"] if both.iloc[i]["truth_a"] == 1 else both.iloc[i]["p_0_a"])
            * (both.iloc[i]["p_1_b"] if both.iloc[i]["truth_b"] == 1 else both.iloc[i]["p_0_b"])
            for i in range(len(both))
        ])
        div = pd.Series(1.0 - p_q_truth, index=both.index)
        trans = q_truth.index[q_truth.ne(q_truth.shift()).values][1:]
        pre = [div.iloc[both.index.get_loc(d) - 20:both.index.get_loc(d)].mean() for d in trans if both.index.get_loc(d) >= 20]
        if pre:
            print(f"    divergence mean {div.mean():.3f}; 20d before transition {np.mean(pre):.3f}  -> {'LEADS' if np.mean(pre) > div.mean() * 1.15 else 'no clear lead'}")
        out = both.copy()
        out["q_pred"], out["q_truth"], out["divergence"] = q_pred, q_truth, div
        out.to_parquet(os.path.join(OUT_DIR, f"{axis}_classifier_{MODEL}_axes_oos.parquet"))


if __name__ == "__main__":
    main()

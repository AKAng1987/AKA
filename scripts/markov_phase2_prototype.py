"""
markov_phase2_prototype.py -- Offline HMM training prototype for Markov Phase 2.

Read-only. No AWS writes. Fits a 4-state GaussianHMM per axis (compass,
grid) on market-implied features, labels hidden states via majority
overlap against model-history ground truth, and reports agreement /
divergence diagnostics. Run this BEFORE building the production Lambda
(M5) so the labeling approach is validated on real data first.

Feature vector (7 dims, all z-scored on the training window):
  vix_level, vix_1d_change
  t10y2y_level, t10y2y_1d_change
  sector_rs_dispersion  -- cross-sectional std of RS_Ratio across whatever
                           SPDR sectors exist that day (9 pre-2018, 10
                           after XLC, 11 after XLRE). Scalar summary, so
                           newer sectors join without leaving NaN holes.
  breadth_proxy         -- share of available sectors closing above 20d SMA
  credit_spread         -- FRED BAA10Y (Moody's Baa corporate yield minus
                           10Y Treasury, daily since 1986, unrestricted).
                           NOT BAMLH0A0HYM2: as of 2026-09 FRED serves ICE
                           BofA series only for the trailing ~3 years
                           (license restriction, count=793 from 2023-09-15
                           regardless of observation_start), which would
                           collapse the training window. HYG-LQD exists only
                           from 2016-09; set CREDIT_SOURCE="hyg_lqd" to
                           compare on that shorter window.

Usage:
  .venv/bin/python scripts/markov_phase2_prototype.py
  CREDIT_SOURCE=hyg_lqd .venv/bin/python scripts/markov_phase2_prototype.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

import boto3
import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import macro_data  # noqa: E402  -- for _fred_get + secret loader

REGION = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"
MODEL_TABLE = "cmon-stage-backend-model-history"

SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
BENCHMARK = "SPY"
CREDIT_SOURCE = os.environ.get("CREDIT_SOURCE", "baa10y")  # "baa10y" | "hyg_lqd"
N_STATES = 4
RANDOM_STATE = 42

# Axis-specific feature subsets. Compass = Liquidity x Credit, so it sees
# rate-curve, credit-spread and vol features. Grid = Growth x Inflation, so
# it sees sector rotation / breadth (growth-sensitive) plus curve + vol.
# Fitting both axes on the identical matrix (the v1 run) produced two HMMs
# that were the same model by construction -- agreement was at base rate.
AXIS_FEATURES = {
    "compass": ["vix_level", "vix_1d_change", "t10y2y_level", "t10y2y_1d_change", "credit_spread"],
    "grid":    ["vix_level", "t10y2y_level", "sector_rs_dispersion", "breadth_proxy", "credit_spread"],
}
USE_AXIS_FEATURES = os.environ.get("AXIS_FEATURES", "1") == "1"
OUT_DIR = os.environ.get(
    "OUT_DIR",
    "/private/tmp/claude-502/-Users-christinatan/28927c47-372d-45ec-a1d2-1ed3c2db4c8b/scratchpad/hmm_proto",
)

_ddb = boto3.client("dynamodb", region_name=REGION)


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_close(symbol: str) -> pd.Series:
    paginator = _ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#s = :s",
        ExpressionAttributeNames={"#s": "symbol", "#d": "date", "#c": "close"},
        ExpressionAttributeValues={":s": {"S": symbol}},
        ProjectionExpression="#d, #c",
    ):
        for it in page["Items"]:
            if "close" in it and "N" in it["close"]:
                rows.append((it["date"]["S"], float(it["close"]["N"])))
    s = pd.Series(dict(rows), name=symbol, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def load_ground_truth(model_name: str) -> pd.Series:
    """Event-driven model-history rows -> daily forward-filled quadrant."""
    paginator = _ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=MODEL_TABLE,
        KeyConditionExpression="model_name = :m",
        ExpressionAttributeValues={":m": {"S": model_name}},
    ):
        for it in page["Items"]:
            rows.append((it["metrics_date"]["S"], int(it["quadrant"]["N"])))
    s = pd.Series(dict(rows), dtype=int)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def load_fred(series_id: str) -> pd.Series:
    df = macro_data._fred_get(series_id)
    s = df.set_index("date")["value"].astype(float)
    s.name = series_id
    return s.sort_index()


# ── Feature engineering ──────────────────────────────────────────────────────

def rs_ratio(sector: pd.Series, bench: pd.Series) -> pd.Series:
    """JdK-style: 100 * raw_rs / SMA_50d(raw_rs). Daily analogue of rrg_data's
    10-week weekly window; scalar dispersion downstream so exact window
    only shifts scale, not structure."""
    raw = (sector / bench).dropna()
    return (100.0 * raw / raw.rolling(50, min_periods=20).mean()).dropna()


def build_features(verbose: bool = True) -> pd.DataFrame:
    if verbose:
        print("Loading VIX, T10Y2Y, SPY...")
    vix = load_close("VIX")
    t10y2y = load_close("T10Y2Y")
    spy = load_close(BENCHMARK)

    if verbose:
        print(f"Loading {len(SECTORS)} sectors...")
    sector_close = {s: load_close(s) for s in SECTORS}

    # Cross-sectional dispersion over available sectors each day.
    rs = pd.DataFrame({s: rs_ratio(c, spy) for s, c in sector_close.items()})
    rs_disp = rs.std(axis=1, skipna=True).rename("sector_rs_dispersion")
    n_avail = rs.notna().sum(axis=1)
    rs_disp[n_avail < 5] = np.nan  # need at least 5 sectors for a meaningful std

    above_sma = pd.DataFrame({
        s: (c > c.rolling(20, min_periods=10).mean()).astype(float).where(c.notna())
        for s, c in sector_close.items()
    })
    breadth = above_sma.mean(axis=1, skipna=True).rename("breadth_proxy")
    breadth[above_sma.notna().sum(axis=1) < 5] = np.nan

    if CREDIT_SOURCE == "hyg_lqd":
        if verbose:
            print("Credit spread: HYG - LQD daily return spread")
        hyg = load_close("HYG").pct_change()
        lqd = load_close("LQD").pct_change()
        credit = (hyg - lqd).rename("credit_spread")
    else:
        if verbose:
            print("Credit spread: FRED BAA10Y (Baa corp - 10Y Treasury)")
        credit = load_fred("BAA10Y").rename("credit_spread")

    feats = pd.concat(
        [
            vix.rename("vix_level"),
            vix.diff().rename("vix_1d_change"),
            t10y2y.rename("t10y2y_level"),
            t10y2y.diff().rename("t10y2y_1d_change"),
            rs_disp,
            breadth,
            credit,
        ],
        axis=1,
    )
    # Business-day forward-fill for series that publish on different
    # calendars (FRED vs. exchange), capped at 5 days so a real gap
    # doesn't get smeared across weeks.
    feats = feats.ffill(limit=5).dropna()
    return feats


def daily_quadrant(events: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill event-driven quadrant changes onto a daily index."""
    return events.reindex(events.index.union(index)).ffill().reindex(index)


# ── HMM fit + label ──────────────────────────────────────────────────────────

def fit_and_label(X: np.ndarray, truth: np.ndarray, axis: str) -> dict:
    model = GaussianHMM(
        n_components=N_STATES,
        covariance_type="full",
        n_iter=300,
        random_state=RANDOM_STATE,
        tol=1e-4,
    )
    model.fit(X)
    hidden = model.predict(X)
    post = model.predict_proba(X)

    # Overlap matrix: rows = hidden state, cols = quadrant 1..4
    overlap = np.zeros((N_STATES, 4), dtype=int)
    for h, q in zip(hidden, truth):
        overlap[h, q - 1] += 1

    # Hungarian assignment maximises total overlap while forcing a
    # one-to-one hidden->quadrant map (majority-overlap can collide).
    row_ind, col_ind = linear_sum_assignment(-overlap)
    label_map = {int(h): int(q + 1) for h, q in zip(row_ind, col_ind)}
    # Also record the naive majority label for transparency.
    majority_map = {h: int(overlap[h].argmax() + 1) for h in range(N_STATES)}

    hmm_q = np.array([label_map[h] for h in hidden])
    agreement = float((hmm_q == truth).mean())

    # P_hmm(current discrete state): pull posterior column matching the
    # hidden state whose label is the truth quadrant.
    inv = {q: h for h, q in label_map.items()}
    p_truth = np.array([post[i, inv[truth[i]]] for i in range(len(truth))])
    divergence = 1.0 - p_truth

    return {
        "axis": axis,
        "model": model,
        "hidden": hidden,
        "posterior": post,
        "overlap": overlap,
        "label_map": label_map,
        "majority_map": majority_map,
        "hmm_q": hmm_q,
        "agreement": agreement,
        "divergence": divergence,
        "converged": bool(model.monitor_.converged),
        "loglik": float(model.score(X)),
    }


def report(res: dict, idx: pd.DatetimeIndex, truth: np.ndarray) -> None:
    axis = res["axis"]
    print(f"\n{'=' * 70}\n{axis.upper()}  --  {idx[0].date()} -> {idx[-1].date()}  ({len(idx)} days)\n{'=' * 70}")
    print(f"converged={res['converged']}  loglik={res['loglik']:.1f}")
    print(f"\nOverlap matrix (rows=hidden state, cols=quadrant 1..4):")
    hdr = "        " + "".join(f"Q{q:<7}" for q in range(1, 5))
    print(hdr)
    for h in range(N_STATES):
        row = "".join(f"{res['overlap'][h, q]:<8}" for q in range(4))
        print(f"  H{h}    {row}  -> Hungarian label Q{res['label_map'][h]}  (majority Q{res['majority_map'][h]})")
    collisions = len(set(res["majority_map"].values())) < N_STATES
    print(f"\nMajority-overlap collision: {collisions}  (Hungarian resolves 1:1)")

    print(f"\nDaily agreement (HMM label == discrete truth): {res['agreement']:.1%}")
    truth_counts = pd.Series(truth).value_counts().sort_index()
    base_rate = float(truth_counts.max() / truth_counts.sum())
    print(f"Majority-class base rate (always guess most common quadrant): {base_rate:.1%}")

    print(f"\nPer-quadrant recall:")
    for q in range(1, 5):
        mask = truth == q
        if mask.sum() == 0:
            print(f"  Q{q}: (no days in truth)")
            continue
        rec = float((res["hmm_q"][mask] == q).mean())
        print(f"  Q{q}: {rec:.1%}  ({mask.sum()} days)")

    div = pd.Series(res["divergence"], index=idx)
    print(f"\ndivergence_score summary: mean={div.mean():.3f} median={div.median():.3f} p90={div.quantile(0.9):.3f}")

    # Divergence around discrete transitions: does the HMM lead?
    truth_s = pd.Series(truth, index=idx)
    trans_dates = truth_s.index[truth_s.ne(truth_s.shift()).values][1:]
    if len(trans_dates) > 0:
        pre = []
        for d in trans_dates:
            loc = idx.get_loc(d)
            if loc >= 20:
                pre.append(div.iloc[loc - 20:loc].mean())
        print(f"\nDiscrete transitions in window: {len(trans_dates)}")
        if pre:
            print(f"Mean divergence in 20 days BEFORE a transition: {np.mean(pre):.3f}  (vs. overall {div.mean():.3f})")
            print(f"  -> {'HMM leads' if np.mean(pre) > div.mean() * 1.1 else 'no clear lead signal'}")

    print("\nTop 5 highest-divergence dates:")
    for d, v in div.nlargest(5).items():
        loc = idx.get_loc(d)
        hmm_top = res["label_map"][int(res["posterior"][loc].argmax())]
        print(f"  {d.date()}  div={v:.3f}  discrete=Q{truth[loc]}  hmm_top=Q{hmm_top}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"=== Markov Phase 2 prototype  (credit_source={CREDIT_SOURCE}) ===\n")

    feat_cache = os.path.join(OUT_DIR, f"features_{CREDIT_SOURCE}.parquet")
    if os.path.exists(feat_cache) and os.environ.get("REBUILD_FEATURES") != "1":
        print(f"Loading cached features: {feat_cache}  (REBUILD_FEATURES=1 to refetch)")
        feats = pd.read_parquet(feat_cache)
    else:
        feats = build_features()
        feats.to_parquet(feat_cache)
        print(f"Cached features: {feat_cache}")
    print(f"\nFeature matrix: {feats.shape[0]} days x {feats.shape[1]} features")
    print(f"  range: {feats.index[0].date()} -> {feats.index[-1].date()}")
    print(f"  columns: {list(feats.columns)}")

    print("\nLoading ground truth...")
    truth_events = {m: load_ground_truth(m) for m in ("compass_US", "grid_US")}
    for m, ev in truth_events.items():
        print(f"  {m}: {len(ev)} events, {ev.index[0].date()} -> {ev.index[-1].date()}, quadrants={sorted(ev.unique())}")

    results = {}
    for axis, model_name in (("compass", "compass_US"), ("grid", "grid_US")):
        ev = truth_events[model_name]
        # Training window = feature history intersected with ground-truth coverage.
        window = feats.loc[feats.index >= ev.index[0]]
        if USE_AXIS_FEATURES:
            window = window[AXIS_FEATURES[axis]]
        truth = daily_quadrant(ev, window.index).dropna()
        window = window.loc[truth.index]
        truth_arr = truth.astype(int).values
        print(f"\n[{axis}] features: {list(window.columns)}")

        mu, sd = window.mean(), window.std(ddof=0).replace(0, 1.0)
        X = ((window - mu) / sd).values

        res = fit_and_label(X, truth_arr, axis)
        report(res, window.index, truth_arr)
        results[axis] = res

        out = window.copy()
        out["truth_q"] = truth_arr
        out["hmm_q"] = res["hmm_q"]
        out["divergence"] = res["divergence"]
        for h in range(N_STATES):
            out[f"p_state_Q{res['label_map'][h]}"] = res["posterior"][:, h]
        path = os.path.join(OUT_DIR, f"{axis}_{CREDIT_SOURCE}.parquet")
        out.to_parquet(path)
        print(f"\nSaved: {path}")

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for axis, res in results.items():
        print(f"  {axis:8s} agreement={res['agreement']:.1%}  converged={res['converged']}  label_map={res['label_map']}")


if __name__ == "__main__":
    main()

"""
markov_features.py -- Feature engineering for the Phase 2 experimental
divergence layer. Imported by BOTH the offline trainer
(scripts/markov_phase2_train.py) and the Lambda handler, so the model is
always scored on exactly the features it was trained on.

All inputs are price-history symbols (equities via marketstack, macro
series via fred-data-updater). No external API calls here.

Feature set matches scripts/markov_phase2_classifier.py, which is where
the walk-forward numbers in MARKOV_PHASE2_PLAN.md s8 came from.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

FEATURE_VERSION = 1

SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
BENCHMARK = "SPY"
INPUT_SYMBOLS = ["VIX", "T10Y2Y", BENCHMARK, "KRE", "COPPER", "DBC", "BAA10Y", "T10YIE", *SECTORS]

FEATURE_NAMES = [
    "vix_level", "vix_1d", "vix_5d",
    "t10y2y_level", "t10y2y_1d", "t10y2y_20d",
    "credit_level", "credit_1d", "credit_20d",
    "kre_rs",
    "xly_xlp_20d", "copper_20d", "sector_rs_dispersion", "breadth_proxy",
    "t10yie_level", "t10yie_1d", "t10yie_20d",
    "dbc_20d",
]

# Longest lookback any feature needs: rs50 (50d rolling on a ratio) +
# the 20d diff on top. 120 calendar days ~ 85 trading days gives margin.
MIN_HISTORY_CALENDAR_DAYS = 120

# Quadrant -> (first_axis_up, second_axis_up). Compass: (Liquidity, Credit).
# Grid: (Growth, Inflation). Same arrow table both axes.
Q_TO_AXES = {1: (1, 0), 2: (1, 1), 3: (0, 1), 4: (0, 0)}


def rs50(num: pd.Series, den: pd.Series) -> pd.Series:
    raw = (num / den).dropna()
    return (100.0 * raw / raw.rolling(50, min_periods=20).mean()).dropna()


def build_features(load_close: Callable[[str], pd.Series]) -> pd.DataFrame:
    """load_close(symbol) -> pd.Series of close indexed by Timestamp, sorted.
    Returns a DataFrame of FEATURE_NAMES, rows = days with all features
    present."""
    vix = load_close("VIX")
    t10y2y = load_close("T10Y2Y")
    spy = load_close(BENCHMARK)
    kre = load_close("KRE")
    copper = load_close("COPPER")
    dbc = load_close("DBC")
    credit = load_close("BAA10Y")
    t10yie = load_close("T10YIE")
    sec = {s: load_close(s) for s in SECTORS}

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
        "vix_level": vix,
        "vix_1d": vix.diff(),
        "vix_5d": vix.diff(5),
        "t10y2y_level": t10y2y,
        "t10y2y_1d": t10y2y.diff(),
        "t10y2y_20d": t10y2y.diff(20),
        "credit_level": credit,
        "credit_1d": credit.diff(),
        "credit_20d": credit.diff(20),
        "kre_rs": rs50(kre, spy),
        "xly_xlp_20d": xly_xlp.pct_change(20),
        "copper_20d": copper.pct_change(20),
        "sector_rs_dispersion": rs_disp,
        "breadth_proxy": breadth,
        "t10yie_level": t10yie,
        "t10yie_1d": t10yie.diff(),
        "t10yie_20d": t10yie.diff(20),
        "dbc_20d": dbc.pct_change(20),
    })
    f = f.replace([np.inf, -np.inf], np.nan).ffill(limit=5).dropna()
    return f[FEATURE_NAMES]

from __future__ import annotations
from typing import Optional, List, Dict
import pandas as pd


def build_regime_periods(grid_df: pd.DataFrame, compass_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert two event-log DataFrames into a unified timeline of
    (start, end, grid_q, compass_q) periods, inclusive on both ends.

    Each row in grid_df / compass_df means "on metrics_date the model changed
    TO quadrant X". We walk all change-event dates chronologically, carry the
    last-known value for each model, and emit one period per interval.
    """
    today = pd.Timestamp.today().normalize()

    grid_map: Dict[pd.Timestamp, int] = {
        row["metrics_date"]: int(row["quadrant"])
        for _, row in grid_df.iterrows()
    }
    compass_map: Dict[pd.Timestamp, int] = {
        row["metrics_date"]: int(row["quadrant"])
        for _, row in compass_df.iterrows()
    }

    all_dates = sorted(set(grid_map.keys()) | set(compass_map.keys()))
    if not all_dates:
        return pd.DataFrame(columns=["start", "end", "grid_q", "compass_q"])

    periods = []
    gq: Optional[int] = None
    cq: Optional[int] = None

    for i, date in enumerate(all_dates):
        if date in grid_map:
            gq = grid_map[date]
        if date in compass_map:
            cq = compass_map[date]
        if gq is None or cq is None:
            continue

        next_date = all_dates[i + 1] if i + 1 < len(all_dates) else today + pd.Timedelta(days=1)
        end = next_date - pd.Timedelta(days=1)
        if date <= end:
            periods.append({"start": date, "end": end, "grid_q": gq, "compass_q": cq})

    return pd.DataFrame(periods)


def get_occurrences(
    prices_df: pd.DataFrame,
    periods_df: pd.DataFrame,
    grid_q: int,
    compass_q: int,
) -> pd.DataFrame:
    """
    For each period matching (grid_q, compass_q), find the price data and compute:
    start_date, end_date, duration_days, entry_close, exit_close,
    high_pct, low_pct, return_pct.

    Entry = close on the first trading day >= period start (signal-date close).
    Exit  = close on the last trading day <= period end.
    """
    if prices_df.empty or periods_df.empty:
        return pd.DataFrame()

    matching = periods_df[
        (periods_df["grid_q"] == grid_q) & (periods_df["compass_q"] == compass_q)
    ]
    if matching.empty:
        return pd.DataFrame()

    results = []
    for _, period in matching.iterrows():
        mask = (prices_df["date"] >= period["start"]) & (prices_df["date"] <= period["end"])
        pp = prices_df[mask]
        if len(pp) < 1:
            continue

        entry_close = float(pp.iloc[0]["close"])
        if not entry_close or entry_close == 0:
            continue

        exit_close  = float(pp.iloc[-1]["close"])
        max_high    = float(pp["high"].max())
        min_low     = float(pp["low"].min())

        results.append({
            "start_date":    pp.iloc[0]["date"],
            "end_date":      pp.iloc[-1]["date"],
            "duration_days": (pp.iloc[-1]["date"] - pp.iloc[0]["date"]).days,
            "entry_close":   round(entry_close, 4),
            "exit_close":    round(exit_close, 4),
            "high_pct":      round((max_high    / entry_close - 1.0) * 100.0, 2),
            "low_pct":       round((min_low     / entry_close - 1.0) * 100.0, 2),
            "return_pct":    round((exit_close  / entry_close - 1.0) * 100.0, 2),
        })

    return pd.DataFrame(results)


def compute_stats(occ_df: pd.DataFrame) -> Optional[Dict]:
    if occ_df.empty:
        return None
    count    = len(occ_df)
    avg_high = float(occ_df["high_pct"].mean())
    avg_low  = float(occ_df["low_pct"].mean())
    hit_rate = float((occ_df["return_pct"] > 0).sum() / count * 100.0)
    # Edge = avg_high / |avg_low|. Meaningful only when avg_low < 0.
    edge = (avg_high / abs(avg_low)) if avg_low < 0 else float("nan")
    avg_return = float(occ_df["return_pct"].mean())
    return {
        "count":        count,
        "avg_high_pct": round(avg_high, 2),
        "avg_low_pct":  round(avg_low, 2),
        "hit_rate":     round(hit_rate, 1),
        "edge":         round(edge, 2) if not pd.isna(edge) else float("nan"),
        "avg_return":   round(avg_return, 2),
    }


def build_backtest_table(
    universe: List[str],
    prices_map: Dict[str, pd.DataFrame],
    periods_df: pd.DataFrame,
    grid_q: int,
    compass_q: int,
    min_count: int = 5,
) -> pd.DataFrame:
    rows = []
    for sym in universe:
        prices = prices_map.get(sym)
        if prices is None or prices.empty:
            continue
        occ   = get_occurrences(prices, periods_df, grid_q, compass_q)
        stats = compute_stats(occ)
        if stats is None or stats["count"] < min_count:
            continue
        rows.append({"Ticker": sym, **stats})
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .rename(columns={
            "count":        "Occurrences",
            "avg_high_pct": "Avg High%",
            "avg_low_pct":  "Avg Low%",
            "hit_rate":     "Hit Rate",
            "edge":         "Edge",
            "avg_return":   "Avg Return%",
        })
        .sort_values("Edge", ascending=False)
        .reset_index(drop=True)
    )


def deep_dive_grid(
    prices_df: pd.DataFrame,
    periods_df: pd.DataFrame,
    min_count: int = 3,
) -> pd.DataFrame:
    """
    Returns a 4×4 DataFrame of edge values (rows=Grid Q 1-4, cols=Compass Q 1-4).
    Cells with insufficient occurrences are NaN.
    """
    data: Dict[int, Dict[int, float]] = {}
    for gq in range(1, 5):
        data[gq] = {}
        for cq in range(1, 5):
            occ   = get_occurrences(prices_df, periods_df, gq, cq)
            stats = compute_stats(occ)
            if stats and stats["count"] >= min_count and not pd.isna(stats["edge"]):
                data[gq][cq] = stats["edge"]
            else:
                data[gq][cq] = float("nan")
    df = pd.DataFrame(data).T  # rows=gq, cols=cq
    df.index.name = "grid_q"
    df.columns = [f"C{c}" for c in df.columns]
    return df


def get_scenario_occurrences(
    prices_df: pd.DataFrame,
    periods_df: pd.DataFrame,
    from_grid: int,
    from_compass: int,
    to_grid: int,
    to_compass: int,
    n_days: int = 30,
) -> pd.DataFrame:
    """
    Find every transition from (from_grid, from_compass) → (to_grid, to_compass)
    in the regime timeline. For each transition, compute stats over the next
    n_days of trading after the transition date.
    """
    if prices_df.empty or periods_df.empty:
        return pd.DataFrame()
    if from_grid == to_grid and from_compass == to_compass:
        return pd.DataFrame()

    results = []
    for i in range(1, len(periods_df)):
        prev = periods_df.iloc[i - 1]
        curr = periods_df.iloc[i]
        if not (int(prev["grid_q"]) == from_grid and int(prev["compass_q"]) == from_compass):
            continue
        if not (int(curr["grid_q"]) == to_grid and int(curr["compass_q"]) == to_compass):
            continue

        transition_date = curr["start"]
        future = prices_df[prices_df["date"] >= transition_date].head(n_days)
        if future.empty:
            continue

        entry_close = float(future.iloc[0]["close"])
        if not entry_close or entry_close == 0:
            continue

        exit_close = float(future.iloc[-1]["close"])
        max_high   = float(future["high"].max())
        min_low    = float(future["low"].min())

        results.append({
            "start_date":    future.iloc[0]["date"],
            "end_date":      future.iloc[-1]["date"],
            "duration_days": len(future),
            "entry_close":   round(entry_close, 4),
            "exit_close":    round(exit_close, 4),
            "high_pct":      round((max_high   / entry_close - 1.0) * 100.0, 2),
            "low_pct":       round((min_low    / entry_close - 1.0) * 100.0, 2),
            "return_pct":    round((exit_close / entry_close - 1.0) * 100.0, 2),
        })

    return pd.DataFrame(results)


def build_scenario_table(
    universe: List[str],
    prices_map: Dict[str, pd.DataFrame],
    periods_df: pd.DataFrame,
    from_grid: int,
    from_compass: int,
    to_grid: int,
    to_compass: int,
    n_days: int = 30,
    min_count: int = 3,
) -> pd.DataFrame:
    rows = []
    for sym in universe:
        prices = prices_map.get(sym)
        if prices is None or prices.empty:
            continue
        occ   = get_scenario_occurrences(prices, periods_df, from_grid, from_compass,
                                          to_grid, to_compass, n_days)
        stats = compute_stats(occ)
        if stats is None or stats["count"] < min_count:
            continue
        rows.append({"Ticker": sym, **stats})
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .rename(columns={
            "count":        "Occurrences",
            "avg_high_pct": "Avg High%",
            "avg_low_pct":  "Avg Low%",
            "hit_rate":     "Hit Rate",
            "edge":         "Edge",
            "avg_return":   "Avg Return%",
        })
        .sort_values("Edge", ascending=False)
        .reset_index(drop=True)
    )

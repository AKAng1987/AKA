"""
rrg_data.py — Relative Rotation Graph data module for CGI dashboard.

Universe
--------
SECTORS (11):     XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC
SUBSECTORS (30):  KBE KRE SMH IBB XHB GDX ITA FDN XRT ICLN KWEB JETS ARKK
                  GDXJ XOP OIH XME MLPX IYT IAI IHI TAN USO CIBR
                  ARKF ARKG ARKQ ARKW SKYY AIQ
COMMODITIES (10): USOIL NATGAS SILVER COPPER RICE COTTON LITHIUM URANIUM NICKEL COAL
REGIONS (10):     EPHE EWJ EWH FXI INDA EIDO VNM EWY EWT EWS

Benchmarks
----------
Sectors + Subsectors + Regions: SPY / QQQ / IWM (user-selectable)
Commodities: DBC (fixed — Invesco DB Commodity Index)

RRG Math (weekly Friday closes, 3-year lookback)
---------
  raw_rs(t)      = ticker_close(t) / benchmark_close(t)
  RS_Ratio(t)    = 100 × raw_rs(t) / SMA_10wk(raw_rs)(t)
  RS_Momentum(t) = 100 × RS_Ratio(t) / SMA_5wk(RS_Ratio)(t)
  Min 16 common weekly observations required (10 + 5 + 1 warmup).

Caches
------
  cache/rrg_equity_{benchmark}.parquet   — sectors + subsectors + regions, 24h
  cache/rrg_commodity_DBC.parquet        — commodities vs DBC, 24h
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import boto3
import pandas as pd
from boto3.dynamodb.types import TypeDeserializer

# ── Constants ──────────────────────────────────────────────────────────────────
CACHE_DIR   = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _configure_aws() -> None:
    """Inject AWS credentials from st.secrets into env vars for boto3.

    On Streamlit Cloud, secrets are not auto-injected into os.environ.
    This bridges the gap so boto3's default credential chain picks them up.
    Falls through silently on local dev (uses ~/.aws/credentials instead).
    """
    try:
        import streamlit as st
        key    = st.secrets.get("AWS_ACCESS_KEY_ID", "")
        secret = st.secrets.get("AWS_SECRET_ACCESS_KEY", "")
        region = st.secrets.get("AWS_DEFAULT_REGION", "")
        if key:
            os.environ.setdefault("AWS_ACCESS_KEY_ID", key)
        if secret:
            os.environ.setdefault("AWS_SECRET_ACCESS_KEY", secret)
        if region:
            os.environ.setdefault("AWS_DEFAULT_REGION", region)
    except Exception:
        pass


_configure_aws()

REGION_AWS  = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"

SECTORS = [
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLU", "XLB", "XLRE", "XLC",
]
SUBSECTORS = [
    # original 13
    "KBE", "KRE", "SMH", "IBB", "XHB", "GDX", "ITA", "FDN",
    "XRT", "ICLN", "KWEB", "JETS", "ARKK",
    # new 17
    "GDXJ", "XOP", "OIH", "XME", "MLPX", "IYT", "IAI", "IHI",
    "TAN", "USO", "CIBR", "ARKF", "ARKG", "ARKQ", "ARKW", "SKYY", "AIQ",
]
COMMODITIES = [
    "USOIL", "NATGAS", "SILVER", "COPPER", "RICE",
    "COTTON", "LITHIUM", "URANIUM", "NICKEL", "COAL",
]
REGIONS = [
    "EPHE", "EWJ", "EWH", "FXI", "INDA",
    "EIDO", "VNM", "EWY", "EWT", "EWS",
]

BENCHMARKS          = ["SPY", "QQQ", "IWM"]
COMMODITY_BENCHMARK = "DBC"

# Tickers with < 5 years of history — rendered smaller with asterisk label
SHORT_HISTORY_TICKERS: frozenset[str] = frozenset({"NICKEL", "COAL"})

# ── Color palettes ─────────────────────────────────────────────────────────────
SECTOR_COLORS: dict[str, str] = {
    "XLK":  "#60A5FA",   # tech — blue
    "XLF":  "#34D399",   # financials — emerald
    "XLE":  "#F59E0B",   # energy — amber
    "XLV":  "#EC4899",   # health — pink
    "XLI":  "#A78BFA",   # industrials — violet
    "XLY":  "#F97316",   # cons disc — orange
    "XLP":  "#D8B4FE",   # cons staples — light purple
    "XLU":  "#8B5CF6",   # utilities — purple
    "XLB":  "#6B7280",   # materials — gray
    "XLRE": "#10B981",   # real estate — green
    "XLC":  "#3B82F6",   # comm services — blue
}

SUBSECTOR_COLORS: dict[str, str] = {
    # original 13
    "KBE":  "#EF4444",   # reg banks — red
    "KRE":  "#B91C1C",   # reg banks 2 — dark red
    "SMH":  "#2563EB",   # semis — deep blue
    "IBB":  "#7C3AED",   # biotech — deep purple
    "XHB":  "#D97706",   # homebuilders — dark amber
    "GDX":  "#B45309",   # gold miners — copper
    "ITA":  "#475569",   # defense — slate
    "FDN":  "#059669",   # internet — emerald
    "XRT":  "#DB2777",   # retail — pink
    "ICLN": "#16A34A",   # clean energy — green
    "KWEB": "#CA8A04",   # China web — gold
    "JETS": "#0891B2",   # airlines — dark cyan
    "ARKK": "#9333EA",   # thematic — violet
    # new 17
    "GDXJ": "#A16207",   # jr gold miners — dark gold
    "XOP":  "#78350F",   # oil E&P — dark brown
    "OIH":  "#92400E",   # oil services — brown
    "XME":  "#52525B",   # metals & mining — zinc
    "MLPX": "#F87171",   # energy MLP — light red
    "IYT":  "#6366F1",   # transport — indigo
    "IAI":  "#22C55E",   # broker-dealers — green
    "IHI":  "#E879F9",   # med devices — fuchsia
    "TAN":  "#FDE047",   # solar — yellow
    "USO":  "#FB923C",   # crude oil ETF — orange
    "CIBR": "#38BDF8",   # cybersecurity — sky
    "ARKF": "#C026D3",   # ARK fintech — magenta
    "ARKG": "#4D7C0F",   # ARK genomic — dark lime
    "ARKQ": "#0369A1",   # ARK autonomous — dark sky
    "ARKW": "#7E22CE",   # ARK next-gen internet — dark purple
    "SKYY": "#1D4ED8",   # cloud — dark blue
    "AIQ":  "#0F766E",   # AI/big data — dark teal
}

COMMODITY_COLORS: dict[str, str] = {
    "USOIL":   "#F59E0B",  # crude — amber
    "NATGAS":  "#92400E",  # nat gas — dark brown
    "SILVER":  "#94A3B8",  # silver — silver-gray
    "COPPER":  "#B45309",  # copper — copper brown
    "RICE":    "#FBBF24",  # rice — light amber
    "COTTON":  "#FCD34D",  # cotton — light yellow
    "LITHIUM": "#C084FC",  # lithium — light purple
    "URANIUM": "#4ADE80",  # uranium — mint green
    "NICKEL":  "#6B7280",  # nickel — gray (short history)
    "COAL":    "#374151",  # coal — dark gray (short history)
}

REGION_COLORS: dict[str, str] = {
    "EPHE": "#22D3EE",   # cyan-400  — Philippines
    "EWJ":  "#06B6D4",   # cyan-500  — Japan
    "EWH":  "#0891B2",   # cyan-600  — Hong Kong
    "FXI":  "#0E7490",   # cyan-700  — China large-cap
    "INDA": "#67E8F9",   # cyan-300  — India
    "EIDO": "#A5F3FC",   # cyan-200  — Indonesia
    "VNM":  "#2DD4BF",   # teal-400  — Vietnam
    "EWY":  "#5EEAD4",   # teal-300  — South Korea
    "EWT":  "#0D9488",   # teal-600  — Taiwan
    "EWS":  "#0F766E",   # teal-700  — Singapore
}

ALL_COLORS: dict[str, str] = {
    **SECTOR_COLORS,
    **SUBSECTOR_COLORS,
    **COMMODITY_COLORS,
    **REGION_COLORS,
}

_ds = TypeDeserializer()


def _deser(item: dict) -> dict:
    return {k: _ds.deserialize(v) for k, v in item.items()}


# ── DynamoDB price fetch ───────────────────────────────────────────────────────

def _fetch_prices(symbols: list[str], lookback_years: int = 3) -> pd.DataFrame:
    """Fetch daily closes for a list of symbols from DynamoDB (last N years).

    Returns a wide DataFrame indexed by date, one column per symbol found.
    Symbols with no data are silently omitted.
    """
    start = (
        datetime.datetime.now() - datetime.timedelta(days=lookback_years * 365 + 30)
    ).strftime("%Y-%m-%d")

    ddb = boto3.client("dynamodb", region_name=REGION_AWS)
    frames: dict[str, pd.Series] = {}

    for sym in symbols:
        rows: list[dict] = []
        paginator = ddb.get_paginator("query")
        for page in paginator.paginate(
            TableName=PRICE_TABLE,
            KeyConditionExpression="#sym = :s AND #d >= :start",
            ExpressionAttributeNames={"#sym": "symbol", "#d": "date"},
            ExpressionAttributeValues={":s": {"S": sym}, ":start": {"S": start}},
        ):
            for item in page["Items"]:
                rows.append(_deser(item))

        if not rows:
            continue

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        close_col = "close" if "close" in df.columns else (
            "adj_close" if "adj_close" in df.columns else None
        )
        if close_col is None:
            continue
        df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
        df = df.dropna(subset=[close_col]).sort_values("date").set_index("date")
        frames[sym] = df[close_col].rename(sym)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames.values(), axis=1).sort_index()


# ── RRG computation ────────────────────────────────────────────────────────────

def _compute_rrg(closes: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    """Core RRG math on a wide closes DataFrame.

    raw_rs(t)      = close(t) / benchmark_close(t)
    RS_Ratio(t)    = 100 × raw_rs / SMA_10wk(raw_rs)
    RS_Momentum(t) = 100 × RS_Ratio / SMA_5wk(RS_Ratio)

    Weekly resampling: W-FRI (last available close on or before each Friday).
    Minimum 16 common weekly observations required (10 + 5 + 1 warmup).

    Returns long DataFrame: ticker | date | RS_Ratio | RS_Momentum
    """
    weekly  = closes.resample("W-FRI").last()
    bench   = weekly[benchmark]
    tickers = [c for c in weekly.columns if c != benchmark]

    out_rows: list[pd.DataFrame] = []
    for ticker in tickers:
        s = weekly[ticker]
        common = s.dropna().index.intersection(bench.dropna().index)
        if len(common) < 16:
            continue
        raw_rs   = s.loc[common] / bench.loc[common]
        rs_ratio = 100.0 * raw_rs / raw_rs.rolling(10, min_periods=10).mean()
        rs_mom   = 100.0 * rs_ratio / rs_ratio.rolling(5, min_periods=5).mean()
        combo    = pd.DataFrame(
            {"ticker": ticker, "RS_Ratio": rs_ratio, "RS_Momentum": rs_mom}
        ).dropna()
        out_rows.append(combo)

    if not out_rows:
        return pd.DataFrame(columns=["ticker", "date", "RS_Ratio", "RS_Momentum"])

    result = pd.concat(out_rows).reset_index().rename(columns={"index": "date"})
    return result.sort_values(["ticker", "date"]).reset_index(drop=True)


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"rrg_{key}.parquet"


def _is_stale(key: str, max_age_hours: float = 24.0) -> bool:
    p = _cache_path(key)
    if not p.exists():
        return True
    return (datetime.datetime.now().timestamp() - p.stat().st_mtime) > max_age_hours * 3600


# ── Public API ─────────────────────────────────────────────────────────────────

def compute_rrg_equity(benchmark: str = "SPY", force: bool = False) -> pd.DataFrame:
    """Compute RRG for SECTORS + SUBSECTORS + REGIONS vs equity benchmark.

    Cached to cache/rrg_equity_{benchmark}.parquet for 24h.
    Separate cache per benchmark preserves commodity cache on equity benchmark changes.
    """
    if benchmark not in BENCHMARKS:
        raise ValueError(f"Unknown benchmark {benchmark!r}; valid: {BENCHMARKS}")
    key = f"equity_{benchmark}"
    if not force and not _is_stale(key):
        df = pd.read_parquet(_cache_path(key))
        df["date"] = pd.to_datetime(df["date"])
        return df
    symbols = SECTORS + SUBSECTORS + REGIONS + [benchmark]
    closes  = _fetch_prices(symbols)
    if closes.empty or benchmark not in closes.columns:
        return pd.DataFrame(columns=["ticker", "date", "RS_Ratio", "RS_Momentum"])
    df = _compute_rrg(closes, benchmark)
    df.to_parquet(_cache_path(key), index=False)
    return df


def compute_rrg_commodity(force: bool = False) -> pd.DataFrame:
    """Compute RRG for COMMODITIES vs DBC.

    Cached to cache/rrg_commodity_DBC.parquet for 24h.
    """
    key = "commodity_DBC"
    if not force and not _is_stale(key):
        df = pd.read_parquet(_cache_path(key))
        df["date"] = pd.to_datetime(df["date"])
        return df
    symbols = COMMODITIES + [COMMODITY_BENCHMARK]
    closes  = _fetch_prices(symbols)
    if closes.empty or COMMODITY_BENCHMARK not in closes.columns:
        return pd.DataFrame(columns=["ticker", "date", "RS_Ratio", "RS_Momentum"])
    df = _compute_rrg(closes, COMMODITY_BENCHMARK)
    df.to_parquet(_cache_path(key), index=False)
    return df


def rrg_cache_info() -> dict[str, dict]:
    """Return {key: {stale: bool, mtime: str}} for sidebar freshness display."""
    keys = [f"equity_{b}" for b in BENCHMARKS] + ["commodity_DBC"]
    result: dict[str, dict] = {}
    for k in keys:
        p     = _cache_path(k)
        stale = _is_stale(k)
        mtime = (
            datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            if p.exists() else "never"
        )
        result[k] = {"stale": stale, "mtime": mtime}
    return result

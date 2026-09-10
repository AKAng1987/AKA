"""
macro_data.py — Macro indicator data layer for the CGI dashboard.
Fetches and caches: GDP (BEA NIPA), Inflation CPI/PPI (BLS v2),
Core PCE (BEA NIPA), Treasury yield curve (FRED DGS),
credit spreads (FRED), and GDPNow (FRED GDPNOW).
"""
from __future__ import annotations

import datetime
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

MAX_AGE_HOURS = 12.0  # panels warn when > 2× this (24 h)


# ── Secrets ───────────────────────────────────────────────────────────────────

def _secret(key: str) -> str:
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"macro_{name}.parquet"


def _is_stale(name: str, max_age_hours: float = MAX_AGE_HOURS) -> bool:
    p = _cache_path(name)
    if not p.exists():
        return True
    age = datetime.datetime.now().timestamp() - p.stat().st_mtime
    return age > max_age_hours * 3600


_STALENESS_HOURS: dict[str, float] = {
    "gdp":              168.0,
    "gdpnow":            24.0,
    "gdp_vintages":     720.0,
    "inflation":        168.0,
    "pce":              168.0,
    "treasury_curve":    24.0,
    "spreads":           24.0,
    "fed_funds_range":   24.0,
    "lending_standards": 168.0,
    "gdp_components":    168.0,
}


def macro_staleness() -> dict[str, bool]:
    return {n: _is_stale(n, h) for n, h in _STALENESS_HOURS.items()}


# ── BEA ───────────────────────────────────────────────────────────────────────

BEA_URL = "https://apps.bea.gov/api/data"


def _bea_get(params: dict) -> dict:
    params["UserID"]       = _secret("BEA_API_KEY")
    params["ResultFormat"] = "JSON"
    resp = requests.get(BEA_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_gdp(force: bool = False) -> pd.DataFrame:
    """BEA NIPA T10101 — QoQ annualized real GDP % change.
    Columns: date (quarterly period-start), gdp_pct."""
    if not force and not _is_stale("gdp"):
        df = pd.read_parquet(_cache_path("gdp"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    data = _bea_get({
        "method":      "GetData",
        "DataSetName": "NIPA",
        "TableName":   "T10101",
        "Frequency":   "Q",
        "Year":        "ALL",
    })
    rows = data["BEAAPI"]["Results"]["Data"]
    records = []
    for r in rows:
        if r.get("LineDescription", "").strip() == "Gross domestic product":
            tp = r["TimePeriod"]          # e.g. "2024Q3"
            year, q = int(tp[:4]), int(tp[5])
            month = (q - 1) * 3 + 1
            try:
                val = float(r["DataValue"].replace(",", ""))
            except (ValueError, AttributeError):
                continue
            records.append({
                "date":    pd.Timestamp(year=year, month=month, day=1),
                "gdp_pct": val,
            })
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    df.to_parquet(_cache_path("gdp"), index=False)
    return df


def fetch_pce(force: bool = False) -> pd.DataFrame:
    """BEA NIPA T20804 -- Core PCE (ex-food-&-energy) chain-type price
    INDEX level (CL_UNIT="Level", ~127-130 on the current base -- NOT a
    pre-computed rate, despite this function's old docstring/column name
    claiming MoM%). Bug found 2026-09-06: the old code stored this index
    level as `pce_core_pct` and callers compounded it as if it were a
    monthly rate, producing ~1.8 million instead of a sane ~2-3% YoY.

    Fix: filter to the exact line "PCE excluding food and energy" (the
    old substring match also caught "Market-based PCE excluding food and
    energy", a different, narrower BEA series -- FRED's canonical
    PCEPILFE tracks the non-market-based line, so that's the one used
    here). Compute YoY correctly as a 12-month index ratio, not a
    compounded rate. Validated against FRED PCEPILFE for 2026-07: this
    function's math produces 3.34414%, FRED reports 3.34414% -- matches
    to 4+ decimal places.

    Columns: date, pce_core_index (raw index level, kept for reference),
    pce_core_yoy (correct YoY %, use this)."""
    if not force and not _is_stale("pce"):
        df = pd.read_parquet(_cache_path("pce"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    data = _bea_get({
        "method":      "GetData",
        "DataSetName": "NIPA",
        "TableName":   "T20804",
        "Frequency":   "M",
        "Year":        "ALL",
    })
    rows = data["BEAAPI"]["Results"]["Data"]
    records = []
    for r in rows:
        if r.get("LineDescription", "") == "PCE excluding food and energy":
            tp = r["TimePeriod"]          # e.g. "2024M07"
            try:
                year  = int(tp[:4])
                month = int(tp[5:7])
                val   = float(r["DataValue"].replace(",", ""))
            except (ValueError, AttributeError, IndexError):
                continue
            records.append({
                "date":           pd.Timestamp(year=year, month=month, day=1),
                "pce_core_index": val,
            })
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    df["pce_core_yoy"] = (df["pce_core_index"] / df["pce_core_index"].shift(12) - 1) * 100
    df.to_parquet(_cache_path("pce"), index=False)
    return df


# ── BLS ───────────────────────────────────────────────────────────────────────

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

BLS_SERIES = {
    "CUUR0000SA0":    "CPI",
    "CUUR0000SA0L1E": "Core CPI",
    "WPUFD49104":     "PPI",
}


def _bls_post(series_ids: list[str], start_year: int, end_year: int) -> dict:
    payload = {
        "seriesid":        series_ids,
        "startyear":       str(start_year),
        "endyear":         str(end_year),
        "registrationkey": _secret("BLS_API_KEY"),
    }
    resp = requests.post(BLS_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_inflation(force: bool = False) -> pd.DataFrame:
    """BLS CPI NSA, Core CPI NSA, PPI NSA — index levels converted to YoY%.
    Columns: date, CPI, Core CPI, PPI."""
    if not force and not _is_stale("inflation"):
        df = pd.read_parquet(_cache_path("inflation"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    series_ids   = list(BLS_SERIES.keys())
    current_year = datetime.datetime.now().year
    long_rows: list[dict] = []

    # BLS caps at 20 years per request — fetch in two 20-year chunks
    for start, end in [
        (current_year - 39, current_year - 20),
        (current_year - 19, current_year),
    ]:
        result = _bls_post(series_ids, start, end)
        for series in result.get("Results", {}).get("series", []):
            sid   = series["seriesID"]
            label = BLS_SERIES.get(sid, sid)
            for obs in series.get("data", []):
                period = obs.get("period", "M00")
                if not period.startswith("M") or period == "M13":
                    continue
                try:
                    month = int(period[1:])
                    val   = float(obs["value"])
                except (ValueError, KeyError):
                    continue
                long_rows.append({
                    "date":   pd.Timestamp(year=int(obs["year"]), month=month, day=1),
                    "series": label,
                    "value":  val,
                })
        time.sleep(0.5)  # respect BLS rate limits

    if not long_rows:
        return pd.DataFrame(columns=["date", "CPI", "Core CPI", "PPI"])

    long = pd.DataFrame(long_rows)
    wide = (
        long.pivot_table(index="date", columns="series", values="value", aggfunc="last")
        .sort_index()
    )

    result_df = pd.DataFrame(index=wide.index)
    for col in ["CPI", "Core CPI", "PPI"]:
        if col in wide.columns:
            result_df[col] = wide[col].pct_change(12) * 100

    result_df = result_df.dropna(how="all").reset_index()
    result_df.to_parquet(_cache_path("inflation"), index=False)
    return result_df


# ── FRED ──────────────────────────────────────────────────────────────────────

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

DGS_SERIES = [
    "DGS1MO", "DGS3MO", "DGS1", "DGS2", "DGS3",
    "DGS5", "DGS7", "DGS10", "DGS20", "DGS30",
]
DGS_LABELS = {
    "DGS1MO": "1M",  "DGS3MO": "3M",
    "DGS1":   "1Y",  "DGS2":   "2Y",  "DGS3":  "3Y",
    "DGS5":   "5Y",  "DGS7":   "7Y",  "DGS10": "10Y",
    "DGS20":  "20Y", "DGS30":  "30Y",
}


def _fred_get(series_id: str, observation_start: Optional[str] = None) -> pd.DataFrame:
    params: dict = {
        "series_id": series_id,
        "api_key":   _secret("FRED_API_KEY"),
        "file_type": "json",
    }
    if observation_start:
        params["observation_start"] = observation_start
    resp = requests.get(FRED_URL, params=params, timeout=30)
    resp.raise_for_status()
    records = []
    for o in resp.json().get("observations", []):
        try:
            records.append({"date": pd.Timestamp(o["date"]), "value": float(o["value"])})
        except (ValueError, KeyError):
            continue
    return pd.DataFrame(records)


def fetch_treasury_curve(force: bool = False) -> pd.DataFrame:
    """FRED DGS1MO … DGS30 — daily yields, 20-year lookback.
    Wide DataFrame: date, 1M, 3M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y."""
    if not force and not _is_stale("treasury_curve"):
        df = pd.read_parquet(_cache_path("treasury_curve"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    start  = (datetime.datetime.now() - datetime.timedelta(days=365 * 20)).strftime("%Y-%m-%d")
    frames = {}
    for sid in DGS_SERIES:
        df_s = _fred_get(sid, observation_start=start)
        if not df_s.empty:
            frames[DGS_LABELS[sid]] = df_s.set_index("date")["value"]

    wide = pd.DataFrame(frames).sort_index().reset_index().rename(columns={"index": "date"})
    wide.to_parquet(_cache_path("treasury_curve"), index=False)
    return wide


def fetch_spreads(force: bool = False) -> pd.DataFrame:
    """FRED T10Y2Y and BAMLH0A0HYM2 — 20-year lookback.
    Columns: date, T10Y2Y (%), HY_Spread (bp).

    BAMLH0A0HYM2 is published by FRED in percent, not bp (verified
    live against FRED 2026-09-10: series metadata units="Percent",
    e.g. 2.71 on 2026-09-09). Every consumer of this column (this
    docstring, the Streamlit chart's legend/axis, and the Next.js
    RatesSection chart) has always labeled it "(bp)" -- the original
    bug was fetching the raw percent value with no x100 conversion,
    so the chart displayed e.g. "2.71 bp" for what was actually a
    271bp spread, a 100x understatement of credit risk. Fixed here at
    the source rather than relabeling three separate display sites to
    "(%)", since bp is the correct convention for quoting a credit
    OAS spread and matches what every label already said."""
    if not force and not _is_stale("spreads"):
        df = pd.read_parquet(_cache_path("spreads"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    start   = (datetime.datetime.now() - datetime.timedelta(days=365 * 20)).strftime("%Y-%m-%d")
    dfs: dict = {}
    for sid, label in [("T10Y2Y", "T10Y2Y"), ("BAMLH0A0HYM2", "HY_Spread")]:
        df_s = _fred_get(sid, observation_start=start)
        if not df_s.empty:
            series = df_s.set_index("date")["value"]
            if label == "HY_Spread":
                series = series * 100.0  # FRED percent -> bp
            dfs[label] = series

    wide = pd.DataFrame(dfs).sort_index().reset_index().rename(columns={"index": "date"})
    wide.to_parquet(_cache_path("spreads"), index=False)
    return wide


def fetch_gdpnow(force: bool = False) -> pd.DataFrame:
    """FRED GDPNOW — Atlanta Fed real-time GDP estimate.
    Columns: date, gdpnow."""
    if not force and not _is_stale("gdpnow"):
        df = pd.read_parquet(_cache_path("gdpnow"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    start  = (datetime.datetime.now() - datetime.timedelta(days=365 * 10)).strftime("%Y-%m-%d")
    df_raw = _fred_get("GDPNOW", observation_start=start)
    if df_raw.empty:
        return pd.DataFrame(columns=["date", "gdpnow"])
    df_raw = df_raw.rename(columns={"value": "gdpnow"})
    df_raw.to_parquet(_cache_path("gdpnow"), index=False)
    return df_raw


# ── Fed Funds Target Range ────────────────────────────────────────────────────

def fetch_fed_funds_range(force: bool = False) -> pd.DataFrame:
    """DFEDTARU (upper) and DFEDTARL (lower) Fed Funds target, last 5 years.
    DFEDTARU: DynamoDB price-history (confirmed present as of 2026-05-23).
    DFEDTARL: FRED only (not in DynamoDB).
    Columns: date, upper, lower."""
    if not force and not _is_stale("fed_funds_range", max_age_hours=24.0):
        df = pd.read_parquet(_cache_path("fed_funds_range"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    start_5y = (datetime.datetime.now() - datetime.timedelta(days=365 * 5)).strftime("%Y-%m-%d")

    # DFEDTARU — prefer DynamoDB
    try:
        import data_cache
        upper_raw = data_cache.load_price("DFEDTARU", force=force)
        if upper_raw.empty:
            raise ValueError("empty DDB result")
        upper_raw = upper_raw[upper_raw["date"] >= pd.Timestamp(start_5y)]
        upper_df  = upper_raw[["date", "close"]].rename(columns={"close": "upper"})
    except Exception:
        raw = _fred_get("DFEDTARU", observation_start=start_5y)
        upper_df = raw.rename(columns={"value": "upper"})

    # DFEDTARL — FRED only
    raw_l   = _fred_get("DFEDTARL", observation_start=start_5y)
    lower_df = raw_l.rename(columns={"value": "lower"})

    df = (
        pd.merge(upper_df, lower_df[["date", "lower"]], on="date", how="outer")
        .sort_values("date")
        .reset_index(drop=True)
    )
    df.to_parquet(_cache_path("fed_funds_range"), index=False)
    return df


# ── Bank Lending Standards ────────────────────────────────────────────────────

def fetch_lending_standards(force: bool = False) -> pd.DataFrame:
    """FRED DRTSCILM — Net % Domestic Banks Tightening C&I Loan Standards (quarterly).
    Columns: date, value."""
    if not force and not _is_stale("lending_standards", max_age_hours=168.0):
        df = pd.read_parquet(_cache_path("lending_standards"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    start = (datetime.datetime.now() - datetime.timedelta(days=365 * 15)).strftime("%Y-%m-%d")
    df = _fred_get("DRTSCILM", observation_start=start)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(_cache_path("lending_standards"), index=False)
    return df


# ── GDP Component Contributions ───────────────────────────────────────────────

_GDP_COMPONENTS = {
    "Personal consumption expenditures":                              "Consumption",
    "Gross private domestic investment":                              "Investment",
    "Net exports of goods and services":                              "Net Exports",
    "Government consumption expenditures and gross investment":       "Government",
}


def fetch_gdp_components(force: bool = False) -> pd.DataFrame:
    """BEA NIPA T10102 — contributions to QoQ annualized real GDP growth (pp).
    Columns: date, Consumption, Investment, Net Exports, Government."""
    if not force and not _is_stale("gdp_components", max_age_hours=168.0):
        df = pd.read_parquet(_cache_path("gdp_components"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    data = _bea_get({
        "method":      "GetData",
        "DataSetName": "NIPA",
        "TableName":   "T10102",
        "Frequency":   "Q",
        "Year":        "ALL",
    })
    rows = data["BEAAPI"]["Results"]["Data"]

    records: dict[pd.Timestamp, dict] = {}
    for r in rows:
        desc = r.get("LineDescription", "").strip()
        label = _GDP_COMPONENTS.get(desc)
        if label is None:
            continue
        tp = r["TimePeriod"]
        year, q = int(tp[:4]), int(tp[5])
        month = (q - 1) * 3 + 1
        date = pd.Timestamp(year=year, month=month, day=1)
        try:
            val = float(r["DataValue"].replace(",", ""))
        except (ValueError, AttributeError):
            continue
        if date not in records:
            records[date] = {"date": date}
        records[date][label] = val

    df = pd.DataFrame(list(records.values())).sort_values("date").reset_index(drop=True)
    df.to_parquet(_cache_path("gdp_components"), index=False)
    return df


# ── GDP Vintages (ALFRED) ─────────────────────────────────────────────────────

# BEA official GDP release dates (Advance / Second / Third).
# Source: BEA press releases at bea.gov/news — stable once published.
# Approach B: hardcoded because bea.gov/news/schedule only shows future releases
# and ALFRED only stores new vintages when the value changes (so Second==Advance
# leaves no ALFRED footprint; we use as-of-date lookup to recover the right value).
#
# Q3-2025 note: US government shutdown delayed normal Oct/Nov releases.
# BEA's first Q3-2025 estimate was published Dec 23, 2025; labeled "Advance" here
# for display continuity. Second estimate followed Jan 22, 2026.
# Add new entries here after each quarterly SEP release.
_BEA_GDP_CALENDAR: dict[str, dict[str, str]] = {
    # quarter_start (ISO) : {vintage_label: release_date (ISO)}
    "2022-01-01": {"Advance": "2022-04-28", "Second": "2022-05-26", "Third": "2022-06-29"},
    "2022-04-01": {"Advance": "2022-07-28", "Second": "2022-08-25", "Third": "2022-09-29"},
    "2022-07-01": {"Advance": "2022-10-27", "Second": "2022-11-30", "Third": "2022-12-22"},
    "2022-10-01": {"Advance": "2023-01-26", "Second": "2023-02-23", "Third": "2023-03-30"},
    "2023-01-01": {"Advance": "2023-04-27", "Second": "2023-05-25", "Third": "2023-06-29"},
    "2023-04-01": {"Advance": "2023-07-27", "Second": "2023-08-30", "Third": "2023-09-28"},
    "2023-07-01": {"Advance": "2023-10-26", "Second": "2023-11-29", "Third": "2023-12-21"},
    "2023-10-01": {"Advance": "2024-01-25", "Second": "2024-02-28", "Third": "2024-03-28"},
    "2024-01-01": {"Advance": "2024-04-25", "Second": "2024-05-30", "Third": "2024-06-27"},
    "2024-04-01": {"Advance": "2024-07-25", "Second": "2024-08-29", "Third": "2024-09-26"},
    "2024-07-01": {"Advance": "2024-10-30", "Second": "2024-11-27", "Third": "2024-12-19"},
    "2024-10-01": {"Advance": "2025-01-30", "Second": "2025-02-27", "Third": "2025-03-27"},
    "2025-01-01": {"Advance": "2025-04-30", "Second": "2025-05-29", "Third": "2025-06-26"},
    "2025-04-01": {"Advance": "2025-07-30", "Second": "2025-08-28", "Third": "2025-09-25"},
    "2025-07-01": {"Advance": "2025-12-23", "Second": "2026-01-22"},   # govt shutdown
    "2025-10-01": {"Advance": "2026-02-20", "Second": "2026-03-13", "Third": "2026-04-09"},
    "2026-01-01": {"Advance": "2026-04-30"},
}


def fetch_gdp_vintages(force: bool = False) -> pd.DataFrame:
    """Fetch ALFRED vintage history for A191RL1Q225SBEA (Real GDP % chg, SAAR).

    Returns one row per (quarter, vintage_label) with columns:
      quarter (Timestamp, quarter-start)  vintage (str: Advance/Second/Third)
      value (float %)  release_date (Timestamp)  days_after_qend (int)

    Labeling uses _BEA_GDP_CALENDAR (hardcoded BEA press-release dates) and
    an as-of-date lookup: for each scheduled release date, we query the ALFRED
    value in effect on that day (most recent vintage with realtime_start ≤ date).
    This correctly handles quarters where ALFRED has no new vintage for the Second
    estimate because the value was unchanged from the Advance.
    """
    if not force and not _is_stale("gdp_vintages", max_age_hours=_STALENESS_HOURS["gdp_vintages"]):
        df = pd.read_parquet(_cache_path("gdp_vintages"))
        df["quarter"]      = pd.to_datetime(df["quarter"])
        df["release_date"] = pd.to_datetime(df["release_date"])
        return df

    params = {
        "series_id":      "A191RL1Q225SBEA",
        "api_key":        _secret("FRED_API_KEY"),
        "file_type":      "json",
        "realtime_start": "1776-07-04",
        "realtime_end":   "9999-12-31",
    }
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=30,
    )
    resp.raise_for_status()

    raw: list[dict] = []
    for o in resp.json().get("observations", []):
        try:
            raw.append({
                "quarter":       pd.Timestamp(o["date"]),
                "realtime_start": pd.Timestamp(o["realtime_start"]),
                "value":          float(o["value"]),
            })
        except (ValueError, TypeError):
            continue

    if not raw:
        empty = pd.DataFrame(columns=["quarter", "vintage", "value", "release_date", "days_after_qend"])
        empty.to_parquet(_cache_path("gdp_vintages"), index=False)
        return empty

    alfred = pd.DataFrame(raw).sort_values(["quarter", "realtime_start"]).reset_index(drop=True)

    def _qend(q: pd.Timestamp) -> pd.Timestamp:
        return q + pd.DateOffset(months=2) + pd.offsets.MonthEnd(0)

    def _value_as_of(qstart: pd.Timestamp, release_dt: pd.Timestamp):
        """ALFRED value for qstart in effect on release_dt (≤ date + 3d tolerance)."""
        sub = alfred[
            (alfred["quarter"] == qstart) &
            (alfred["realtime_start"] <= release_dt + pd.Timedelta(days=3))
        ]
        return float(sub.iloc[-1]["value"]) if not sub.empty else None

    # Build output from BEA release calendar
    out_rows: list[dict] = []
    for qs, releases in _BEA_GDP_CALENDAR.items():
        qstart = pd.Timestamp(qs)
        qe     = _qend(qstart)
        for label, rdate_str in releases.items():
            rdate = pd.Timestamp(rdate_str)
            val   = _value_as_of(qstart, rdate)
            if val is None:
                continue
            out_rows.append({
                "quarter":       qstart,
                "vintage":       label,
                "value":         val,
                "release_date":  rdate,
                "days_after_qend": int((rdate - qe).days),
            })

    df_out = (
        pd.DataFrame(out_rows)
        .sort_values(["quarter", "release_date"])
        .reset_index(drop=True)
    )
    df_out.to_parquet(_cache_path("gdp_vintages"), index=False)
    return df_out

"""
fomc_data.py — FOMC meeting calendar, CME FedWatch-style rate probabilities,
and SEP dot-plot loader.

Key design decisions (documented here for maintenance):
  - Monthly Fed Funds Futures: yfinance ZQ{code}{yr}.CBT format (e.g. ZQM26.CBT).
    All 12 forward months confirmed working as of 2026-05-24.
  - FOMC calendar: 2026 dates hardcoded from federalreserve.gov/monetarypolicy/
    fomccalendars.htm (scraped 2026-05-24). 2027+ dates scraped live; fallback to
    hardcoded if scrape fails.
  - SEP dot-plot: Fed publishes HTML (JS-rendered) and PDF only — not machine-
    parseable without a headless browser. Use data_manual/dot_plot.csv, updated
    manually after each quarterly SEP release.
"""
from __future__ import annotations

import calendar
import datetime
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

DOT_PLOT_PATH = Path(__file__).parent / "data_manual" / "dot_plot.csv"

# ── FOMC meeting announcement dates (last day of the meeting) ─────────────────
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Dates are the announcement/statement day (second day of the 2-day meeting).
# New rate takes effect the NEXT business day.
FOMC_2026 = [
    datetime.date(2026, 1, 28),   # done
    datetime.date(2026, 3, 18),   # done (SEP)
    datetime.date(2026, 4, 29),   # done
    datetime.date(2026, 6, 17),   # upcoming
    datetime.date(2026, 7, 29),   # upcoming
    datetime.date(2026, 9, 16),   # upcoming (SEP)
    datetime.date(2026, 10, 28),  # upcoming
    # December 2026 TBD — not yet published on Fed calendar
]

# ZQ futures month codes (CME 30-day Fed Funds)
_MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
_FREQ_DAILY     = 24
_FREQ_QUARTERLY = 168


# ── Secrets ───────────────────────────────────────────────────────────────────

def _secret(key: str) -> str:
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"fomc_{name}.parquet"


def _is_stale(name: str, max_age_hours: float = _FREQ_DAILY) -> bool:
    p = _cache_path(name)
    if not p.exists():
        return True
    age = datetime.datetime.now().timestamp() - p.stat().st_mtime
    return age > max_age_hours * 3600


def _fred_get(series_id: str, observation_start: Optional[str] = None,
              limit: Optional[int] = None) -> pd.DataFrame:
    params: dict = {
        "series_id": series_id,
        "api_key":   _secret("FRED_API_KEY"),
        "file_type": "json",
    }
    if observation_start:
        params["observation_start"] = observation_start
    if limit:
        params["sort_order"] = "desc"
        params["limit"]      = limit
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params, timeout=20,
    )
    resp.raise_for_status()
    records = []
    for o in resp.json().get("observations", []):
        try:
            records.append({"date": pd.Timestamp(o["date"]), "value": float(o["value"])})
        except (ValueError, KeyError):
            continue
    return pd.DataFrame(records)


# ── FOMC meeting calendar ─────────────────────────────────────────────────────

def _scrape_fomc_dates() -> list[datetime.date]:
    """Try to scrape 2027+ meeting dates from the Fed calendar page."""
    try:
        import re
        resp = requests.get(
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            timeout=10,
        )
        resp.raise_for_status()
        raw = re.findall(r"20[2-9][0-9][01][0-9][0-3][0-9]", resp.text)
        dates = []
        for s in sorted(set(raw)):
            try:
                d = datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                dates.append(d)
            except ValueError:
                continue
        return dates
    except Exception:
        return []


def get_upcoming_meetings(
    today: Optional[datetime.date] = None,
    horizon_months: int = 12,
) -> list[datetime.date]:
    """Return FOMC announcement dates from today through today+horizon_months."""
    if today is None:
        today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=horizon_months * 31)

    candidates = list(FOMC_2026)

    # Supplement with scraped dates for future years
    scraped = _scrape_fomc_dates()
    known = set(candidates)
    for d in scraped:
        if d.year > 2026 and d not in known:
            candidates.append(d)
            known.add(d)

    return sorted(d for d in candidates if today <= d <= cutoff)


# ── EFFR / DFEDTARL fetchers ──────────────────────────────────────────────────

def fetch_effr(force: bool = False) -> pd.DataFrame:
    """FRED DFF — Effective Federal Funds Rate, last 30 days.
    Columns: date, value."""
    if not force and not _is_stale("effr", max_age_hours=_FREQ_DAILY):
        df = pd.read_parquet(_cache_path("effr"))
        df["date"] = pd.to_datetime(df["date"])
        return df
    start = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    df = _fred_get("DFF", observation_start=start)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(_cache_path("effr"), index=False)
    return df


def fetch_dfedtarl(force: bool = False) -> pd.DataFrame:
    """FRED DFEDTARL — lower Fed Funds target, last 5 years.
    Columns: date, value."""
    if not force and not _is_stale("dfedtarl", max_age_hours=_FREQ_DAILY):
        df = pd.read_parquet(_cache_path("dfedtarl"))
        df["date"] = pd.to_datetime(df["date"])
        return df
    start = (datetime.datetime.now() - datetime.timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    df = _fred_get("DFEDTARL", observation_start=start)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(_cache_path("dfedtarl"), index=False)
    return df


# ── Fed Funds Futures ─────────────────────────────────────────────────────────

def _futures_ticker(year: int, month: int) -> str:
    return f"ZQ{_MONTH_CODE[month]}{str(year)[-2:]}.CBT"


def fetch_futures_for_meetings(
    meetings: list[datetime.date],
    force: bool = False,
) -> dict[datetime.date, float]:
    """Return {meeting_date: implied_avg_rate_%} from ZQ monthly contracts.
    Uses yfinance ZQ{code}{yr}.CBT — confirmed working for all 12 months."""
    import yfinance as yf

    result: dict[datetime.date, float] = {}
    for mtg in meetings:
        ticker = _futures_ticker(mtg.year, mtg.month)
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if hist.empty:
                continue
            price = float(hist["Close"].iloc[-1])
            result[mtg] = round(100.0 - price, 6)
        except Exception:
            continue
    return result


# ── FedWatch probability calculator ───────────────────────────────────────────

def compute_fomc_probs(
    meetings: list[datetime.date],
    effr_df: pd.DataFrame,
    upper_target: float,
    lower_target: float,
    futures: dict[datetime.date, float],
) -> list[dict]:
    """CME FedWatch methodology.
    For each meeting in `meetings` (with a futures price available):
      1. The meeting-month futures settle at the calendar-month average EFFR.
      2. Split the month at the announcement date:
         days_before (at pre-meeting rate) + days_after (at post-meeting rate)
         = total_days × implied_avg
      3. Solve for implied post-meeting EFFR.
      4. Compare to current target midpoint ± 25bp increments.
      5. Probability = linear interpolation between adjacent 25bp levels.

    Returns list of dicts: {date, ticker, implied_avg, pre_rate, post_rate,
                             p_cut, p_hold, p_hike, most_likely, prob_most_likely}
    """
    if effr_df.empty:
        current_effr = (upper_target + lower_target) / 2
    else:
        current_effr = float(effr_df.sort_values("date").iloc[-1]["value"])

    midpoint  = (upper_target + lower_target) / 2
    increment = 0.25

    results  = []
    pre_rate = current_effr  # chains forward through meetings

    for mtg in sorted(meetings):
        if mtg not in futures:
            continue

        implied_avg = futures[mtg]
        total_days  = calendar.monthrange(mtg.year, mtg.month)[1]
        # New rate takes effect day AFTER announcement
        days_before = mtg.day          # days 1..announcement_day at pre_rate
        days_after  = total_days - mtg.day  # days after announcement at post_rate

        if days_after <= 0:
            # Meeting on last day of month — effectively the whole month is pre-rate
            post_rate = implied_avg
        else:
            post_rate = (implied_avg * total_days - pre_rate * days_before) / days_after

        # Probability relative to CURRENT midpoint (before this meeting)
        current_mid = midpoint  # fixed to current target, not chained
        level_cut   = current_mid - increment
        level_hike  = current_mid + increment

        if post_rate <= current_mid:
            p_cut  = min(1.0, max(0.0, (current_mid - post_rate) / increment))
            p_hold = 1.0 - p_cut
            p_hike = 0.0
        else:
            p_hike = min(1.0, max(0.0, (post_rate - current_mid) / increment))
            p_hold = 1.0 - p_hike
            p_cut  = 0.0

        if p_hold >= max(p_cut, p_hike):
            most_likely, prob_ml = "Hold", p_hold
        elif p_cut > p_hike:
            most_likely, prob_ml = "Cut 25bp", p_cut
        else:
            most_likely, prob_ml = "Hike 25bp", p_hike

        results.append({
            "date":            mtg,
            "ticker":          _futures_ticker(mtg.year, mtg.month),
            "implied_avg":     round(implied_avg, 4),
            "pre_rate":        round(pre_rate,    4),
            "post_rate":       round(post_rate,   4),
            "p_cut":           round(p_cut,       4),
            "p_hold":          round(p_hold,      4),
            "p_hike":          round(p_hike,      4),
            "most_likely":     most_likely,
            "prob_most_likely": round(prob_ml,    4),
        })

        # Chain: expected pre-rate for next meeting = probability-weighted post-rate
        pre_rate = p_cut * (current_mid - increment) + p_hold * current_mid + p_hike * (current_mid + increment)

    return results


# ── Dot Plot ─────────────────────────────────────────────────────────────────

def load_dot_plot() -> pd.DataFrame:
    """Load SEP dot-plot from data_manual/dot_plot.csv.
    Columns: year (str), participant_id (int), projected_rate (float).
    Returns empty DataFrame if file missing."""
    if not DOT_PLOT_PATH.exists():
        return pd.DataFrame(columns=["year", "participant_id", "projected_rate"])
    df = pd.read_csv(DOT_PLOT_PATH, comment="#")
    df["year"]           = df["year"].astype(str)
    df["participant_id"] = pd.to_numeric(df["participant_id"], errors="coerce")
    df["projected_rate"] = pd.to_numeric(df["projected_rate"], errors="coerce")
    return df.dropna().reset_index(drop=True)

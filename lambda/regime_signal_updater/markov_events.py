"""
markov_events.py -- Phase 1.5 pre-registration for regime-signal-updater.

Computes, at 00:55 UTC, one entry per upcoming release (FOMC/SLOOS/CPI/
GDP): the historical P(flip) for its axis in its current state, and the
market-implied P(flip) where one exists. Written onto the daily row as
`upcoming_releases` so the numbers are in DynamoDB BEFORE the print, and
the MARKOV tab's event log scores stored values rather than recomputing.

Stdlib + boto3 only (this Lambda bundles no wheels). The calendar and the
flip-rate math mirror ~/cgi-vercel/api/release_calendar.py and
markov_data.py; the stored row carries the dates it used, so any drift
between the two copies is visible in the record rather than silent.

Market reads (all best-effort; a missing read stores null, never raises):
  liquidity  <- s3://<reports>/Cache/macro/fomc_probabilities.json, the
                MACRO tab's cached fed-funds-futures computation (FedWatch
                method). flip = p_hike when easing, p_cut when tightening.
  credit,    <- previous day's regime-signals row, experimental_divergence
  inflation     (written 01:05 UTC by regime-divergence-updater, so at
                00:55 the latest is yesterday's). flip = 1 - P(up) when up.
  growth     <- none yet (GDPNow is a level, not a flip probability).
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Optional

import boto3

REGION = "ap-southeast-1"
REPORTS_BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
FOMC_PROBS_KEY = "Cache/macro/fomc_probabilities.json"
SIGNALS_TABLE = "cmon-stage-backend-regime-signals"

AXIS_OF = {"FOMC": "liquidity", "SLOOS": "credit", "CPI": "inflation", "GDP": "growth"}
MODEL_OF = {"liquidity": "compass", "credit": "compass", "growth": "grid", "inflation": "grid"}
SLOT_OF = {"liquidity": 0, "credit": 1, "growth": 0, "inflation": 1}
PER_YEAR = {"FOMC": 8, "SLOOS": 4, "CPI": 12, "GDP": 12}
AXES_OF_MODEL = {"compass": ("liquidity", "credit"), "grid": ("growth", "inflation")}
TYPE_OF_AXIS = {v: k for k, v in AXIS_OF.items()}
Q_TO_AXES = {1: (1, 0), 2: (1, 1), 3: (0, 1), 4: (0, 0)}
AXES_TO_Q = {v: k for k, v in Q_TO_AXES.items()}
P_MIN, P_MAX = 0.02, 0.98

# Official schedules -- see ~/cgi-vercel/api/release_calendar.py for URLs.
CALENDAR = sorted(
    [{"date": d, "type": "CPI"} for d in [
        "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10",
        "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-10"]]
    + [{"date": d, "type": "GDP"} for d in [
        "2026-01-29", "2026-02-26", "2026-03-26", "2026-04-29", "2026-05-28", "2026-06-25",
        "2026-07-30", "2026-08-27", "2026-09-25", "2026-10-29", "2026-11-25", "2026-12-22"]]
    + [{"date": d, "type": "FOMC"} for d in [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]]
    + [{"date": d, "type": "SLOOS"} for d in ["2026-02-02", "2026-05-04", "2026-08-03", "2026-11-02"]],
    key=lambda r: r["date"],
)

_s3 = boto3.client("s3", region_name=REGION)
_ddb = boto3.resource("dynamodb", region_name=REGION)


def _state(q: int, axis: str) -> int:
    return Q_TO_AXES[q][SLOT_OF[axis]]


def _with(q: int, axis: str, s: int) -> int:
    a = list(Q_TO_AXES[q])
    a[SLOT_OF[axis]] = s
    return AXES_TO_Q[tuple(a)]


def next_releases(after: str) -> list[dict]:
    out, seen = [], set()
    for r in CALENDAR:
        if r["date"] <= after or r["type"] in seen:
            continue
        seen.add(r["type"])
        out.append(r)
    return out


def flip_rates(model: str, rows: list[dict], today: str) -> dict:
    """rows: ascending [{date, quadrant}] for one model. Returns
    {axis: {state: {p_flip, n_flips, expected_releases, dwell_days}}}.
    Double-flip rows (grid, old GDP-day batching) count once per axis."""
    axes = AXES_OF_MODEL[model]
    flips = {a: {0: 0, 1: 0} for a in axes}
    dwell = {a: {0: 0.0, 1: 0.0} for a in axes}
    for i, r in enumerate(rows):
        end = rows[i + 1]["date"] if i + 1 < len(rows) else today
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(r["date"])).days
        for a in axes:
            dwell[a][_state(r["quadrant"], a)] += days
        if i > 0:
            pq = rows[i - 1]["quadrant"]
            for a in axes:
                s0, s1 = _state(pq, a), _state(r["quadrant"], a)
                if s0 != s1:
                    flips[a][s0] += 1
    out = {}
    for a in axes:
        out[a] = {}
        for s in (0, 1):
            expected = dwell[a][s] / 365.0 * PER_YEAR[TYPE_OF_AXIS[a]]
            p = flips[a][s] / expected if expected > 0 else 0.5
            out[a][s] = {
                "p_flip": min(P_MAX, max(P_MIN, p)),
                "n_flips": flips[a][s],
                "expected_releases": round(expected, 1),
                "dwell_days": round(dwell[a][s], 1),
            }
    return out


def _market_liquidity(fomc_date: str, state: int) -> Optional[dict]:
    try:
        body = _s3.get_object(Bucket=REPORTS_BUCKET, Key=FOMC_PROBS_KEY)["Body"].read()
        probs = json.loads(body)
    except Exception as exc:  # noqa: BLE001
        print(f"[markov_events] fomc_probabilities read failed: {exc}")
        return None
    for p in probs.get("probabilities", []):
        if p.get("date") == fomc_date:
            p_flip = p["p_hike"] if state == 1 else p["p_cut"]
            return {
                "p_flip": round(float(p_flip), 3),
                "source": "fed funds futures (FedWatch method)",
                "detail": f"hike {round(p['p_hike']*100)} / hold {round(p['p_hold']*100)} / cut {round(p['p_cut']*100)}",
                "experimental": False,
            }
    print(f"[markov_events] no fomc_probabilities entry for {fomc_date}")
    return None


def _yesterday_divergence(today: str) -> Optional[dict]:
    y = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    try:
        table = _ddb.Table(SIGNALS_TABLE)
        resp = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("signal_date").eq(y),
            ProjectionExpression="experimental_divergence, signal_date",
        )
        items = resp.get("Items", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[markov_events] divergence read failed: {exc}")
        return None
    for it in items:
        if it.get("experimental_divergence"):
            return it["experimental_divergence"]
    return None


def _market_from_divergence(div: Optional[dict], axis: str, state: int) -> Optional[dict]:
    if not div:
        return None
    a = (div.get("axes") or {}).get(axis)
    if not a or a.get("p_up") is None:
        return None
    p_up = float(a["p_up"])
    return {
        "p_flip": round(1.0 - p_up if state == 1 else p_up, 3),
        "source": f"experimental classifier ({div.get('model_version')}, features {div.get('features_as_of')})",
        "detail": f"P(up)={p_up:.2f}",
        "experimental": True,
    }


def _dec(x):
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, dict):
        return {k: _dec(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_dec(v) for v in x]
    return x


def build_upcoming(history: dict[str, list[dict]], current: dict[str, int], today: str) -> list[dict]:
    """history: {"compass": rows, "grid": rows}; current: {"compass": q, "grid": q}.
    Returns the list to store as `upcoming_releases` (Decimal-safe)."""
    rates = {}
    for model, rows in history.items():
        rates.update(flip_rates(model, rows, today))
    div = _yesterday_divergence(today)
    out = []
    for r in next_releases(today):
        axis = AXIS_OF[r["type"]]
        model = MODEL_OF[axis]
        q = current[model]
        s = _state(q, axis)
        hist = rates[axis][s]
        if axis == "liquidity":
            market = _market_liquidity(r["date"], s)
        elif axis in ("credit", "inflation"):
            market = _market_from_divergence(div, axis, s)
        else:
            market = None
        out.append(_dec({
            "date": r["date"],
            "type": r["type"],
            "axis": axis,
            "model": model,
            "current_quadrant": q,
            "current_state": s,
            "if_flip_quadrant": _with(q, axis, 1 - s),
            "history": {"p_flip": round(hist["p_flip"], 3), "n_flips": hist["n_flips"],
                        "expected_releases": hist["expected_releases"], "dwell_days": hist["dwell_days"]},
            "market": market,
            "gap": round(market["p_flip"] - hist["p_flip"], 3) if market else None,
        }))
    return out

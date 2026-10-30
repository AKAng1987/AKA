"""
cgi_health.py -- one-screen health check of CGI, from the public site only (no token, no AWS).

Run:  python3 scripts/cgi_health.py
Prints a green / amber / red verdict and ~8 lines: refresh routine, TradingView referee, pipeline lag, stalled
feeds, TAPE rows behind, and the regime with the next release that can flip it. Deterministic: no model reasoning,
so the skill that wraps it costs almost nothing.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

SITE = "https://cgi-vercel.vercel.app"
TIMEOUT = 150   # the API sleeps when idle; the first call can take ~75s


def _get(path: str) -> str:
    with urllib.request.urlopen(SITE + path, timeout=TIMEOUT) as r:
        return r.read().decode()


# Rows that lag by design, so they are not a fault: FRED publishes these FX pairs weekly, PH10Y is a weekly manual
# series. Futures and anything else 1 trading day behind are normal too (their close is final a day later).
EXPECTED_WEEKLY = {"USDCAD", "USDMXN", "USDZAR", "USDBRL", "USDTWD", "USDMYR", "PH10Y"}


def _sessions_between(a: str, b: str) -> int:
    import datetime as dt
    d, e, n = dt.date.fromisoformat(a), dt.date.fromisoformat(b), 0
    while d < e:
        d += dt.timedelta(days=1)
        n += d.weekday() < 5
    return n


def tape_behind() -> list[tuple[str, str]]:
    """TAPE rows more than one trading day older than the newest close, excluding series that are weekly by design."""
    h = _get("/tape").replace('\\"', '"')
    rows = re.findall(r'"symbol":"([A-Z0-9_.=^]+)","sector":"[^"]*".*?"as_of":"(\d{4}-\d{2}-\d{2})"', h)
    rows = list(dict(rows).items())
    if not rows:
        return []
    newest = max(d for _, d in rows)
    # Monthly/weekly series (as_of more than ~10 days old by design) are not "behind"; only daily rows lagging.
    recent = [(s, d) for s, d in rows
              if d >= _days_before(newest, 10) and _sessions_between(d, newest) > 1 and s not in EXPECTED_WEEKLY]
    return sorted(recent, key=lambda x: x[1])


def _days_before(iso: str, n: int) -> str:
    import datetime as dt
    return (dt.date.fromisoformat(iso) - dt.timedelta(days=n)).isoformat()


def main() -> int:
    red, amber, lines = [], [], []
    try:
        f = json.loads(_get("/api/freshness"))
    except Exception as exc:  # noqa: BLE001
        print(f"RED   site unreachable: {exc}")
        return 2

    beat = f.get("refresh_routine", {})
    age = beat.get("age_hours")
    lines.append(f"refresh   last run {age}h ago: {(beat.get('ran') or 'no run recorded')[:90]}")
    if beat.get("stale"):
        red.append("refresh routine has not run in 60h+")

    ref = f.get("referee", {})
    lines.append(f"referee   {ref.get('checked', 0)} checked vs TradingView, {ref.get('n_problems', 0)} problems"
                 + (f" ({ref.get('detail')})" if not ref.get("checked") and ref.get("detail") else ""))
    for p in ref.get("problems", []):
        red.append(f"referee {p['check']} {p['symbol']}: {p['detail']}")
    if not ref.get("checked"):
        amber.append("referee received no TradingView bars from the last refresh")

    pl = f.get("pipeline_lag", {})
    lines.append(f"pipeline  {pl.get('n_problems', 0)} problems (workbook vs stored prices)")
    for p in pl.get("problems", []):
        red.append(f"pipeline {p['check']} {p['symbol']}: {p['detail']}")

    fi = f.get("feed_integrity", {})
    stalled = fi.get("stalled", [])
    lines.append(f"feeds     {fi.get('checked', 0)} checked, {len(stalled)} stalled, "
                 f"{len(fi.get('orphans', []))} orphans, {len(fi.get('collisions', []))} collisions")
    for s in stalled:
        amber.append(f"stalled {s['symbol']} ({s.get('source')}): newest {s.get('newest')}, {s.get('behind')}")

    cot = f.get("cot")
    if cot:
        lines.append(f"cot       CFTC newest {cot.get('source_newest')} (published ~{cot.get('published_est')}), we serve {cot.get('ours_as_of')}"
                     f", pattern {cot.get('pattern')}, {cot.get('n_problems', 0)} problems")
        for p in cot.get("problems", []):
            amber.append(f"COT {p.get('check')}: {p.get('detail')}")
        if cot.get("error"):
            amber.append(f"COT check could not run: {cot['error']}")

    capex = f.get("capex")
    if capex:
        lines.append(f"capex     snapshot built {capex.get('built_at')}, expects {capex.get('expected_quarter')}"
                     f" ({capex.get('share_with_expected')} of {capex.get('n_reporters')} reporters have it), {capex.get('n_problems', 0)} problems")
        for p in capex.get("problems", []):
            amber.append(f"capex {p.get('check')}: {p.get('detail')}")
        if capex.get("error"):
            amber.append(f"capex check could not run: {capex['error']}")

    rc = f.get("release_calendar")
    if rc:
        nx = rc.get("next") or {}
        lines.append(f"calendar  next FOMC {nx.get('FOMC')}, CPI {nx.get('CPI')}, GDP {nx.get('GDP')}, SLOOS {nx.get('SLOOS')}; "
                     f"dates run to {rc.get('last_date')}, {rc.get('n_problems', 0)} problems")
        for p in rc.get("problems", []):
            amber.append(f"release calendar {p.get('check')}: {p.get('detail')}")
        if rc.get("error"):
            amber.append(f"release calendar check could not run: {rc['error']}")

    rp = f.get("rate_path")
    if rp:
        lines.append(f"ratepath  {rp.get('n_meetings_priced')} FOMC meetings priced, {rp.get('n_sr3_quoted')} SOFR futures quoted, "
                     f"{rp.get('history_days')} days of snapshots, {rp.get('n_problems', 0)} problems")
        for p in rp.get("problems", []):
            amber.append(f"rate path {p.get('check')}: {p.get('detail')}")
        if rp.get("error"):
            amber.append(f"rate path check could not run: {rp['error']}")

    try:
        behind = tape_behind()
        lines.append(f"tape      {len(behind)} daily rows >1 trading day behind (weekly series excluded)"
                     + (": " + ", ".join(f"{s} {d}" for s, d in behind[:12]) if behind else ""))
        for sym, d in behind:
            amber.append(f"TAPE {sym} stuck at {d}")
    except Exception as exc:  # noqa: BLE001
        amber.append(f"could not read TAPE: {exc}")

    try:
        brief = json.loads(_get("/api/brief?cadence=daily"))
        reg = next((s for s in brief.get("sections", []) if s.get("name") == "regime"), None)
        lines.append(f"regime    {reg['headline'] if reg else 'unavailable'}")
    except Exception as exc:  # noqa: BLE001
        amber.append(f"could not read brief: {exc}")

    verdict = "RED" if red else "AMBER" if amber else "GREEN"
    print(f"{verdict}  CGI health, as of {f.get('as_of')}")
    for l in lines:
        print("  " + l)
    for r in red:
        print("  ! " + r)
    for a in amber:
        print("  ~ " + a)
    return 0 if verdict == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())

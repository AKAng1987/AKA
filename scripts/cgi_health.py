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


def extra_sections(f: dict, lines: list, amber: list) -> None:
    """Sections of /api/freshness that health used to skip (added 2026-11-25, handoff 18): a problem the gate counts must show here.
    On 2026-11-24 a BITO split alert in price_jumps made safe_to_act_on false while health said GREEN."""
    labels = (("price_jumps", "price jump"), ("peer_lag", "peer lag"), ("erp", "erp"), ("earnings_calendar", "earnings calendar"),
              ("filings", "filings"), ("automated", "fred"), ("manual", "manual"))
    for key, label in labels:
        sec = f.get(key)
        if not isinstance(sec, dict):
            continue
        n = sec.get("n_problems", 0) or sec.get("n_never_loaded", 0)
        lines.append(f"{label:<9} {n} problems" + (f" ({sec.get('checked')} checked)" if sec.get("checked") is not None else ""))
        items = []
        if key == "automated":
            bad = set(sec.get("problems", []))   # the server's own verdict: leads_source (a faster provider) is "not a defect"
            items = [f"fred {s['symbol']}: {s.get('detail', s.get('status'))}" for s in sec.get("series", [])
                     if s.get("symbol") in bad]
        elif key == "manual":
            if sec.get("n_never_loaded"):
                items = [f"manual never loaded: {sec.get('unresolved')}"]
        elif key == "erp":
            if sec.get("behind"):
                items = [f"erp behind its source: newest {sec.get('newest_month')}, expected {sec.get('expected_month')}"]
        else:
            items = [f"{label} {p.get('symbol') or p.get('check') or ''}: {p.get('detail', p)}".replace("  ", " ")
                     for p in sec.get("problems", [])]
        if n and not items:
            items = [f"{label}: {n} problems reported, no detail given"]
        amber.extend(items)
        if sec.get("error"):
            amber.append(f"{label} check could not run: {sec['error']}")


def main() -> int:
    as_json = "--json" in sys.argv   # machine-readable verdict for cgi_repair.py; no events are logged in this mode
    red, amber, lines = [], [], []
    try:
        f = json.loads(_get("/api/freshness"))
    except Exception as exc:  # noqa: BLE001
        if as_json:
            print(json.dumps({"verdict": "RED", "red": [f"site unreachable: {exc}"], "amber": [], "as_of": None}))
            return 2
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

    gd = f.get("gdp")
    if gd:
        lines.append(f"gdp       latest release {gd.get('latest_release')}, newest stored {gd.get('newest_stored')}, {gd.get('n_problems', 0)} problems")
        for p in gd.get("problems", []):
            amber.append(f"gdp {p.get('check')}: {p.get('detail')}")
        if gd.get("error"):
            amber.append(f"gdp check could not run: {gd['error']}")

    ph = f.get("ph")
    if ph:
        lines.append(f"ph        prices to {ph.get('prices_asof')}, {ph.get('n_liquid')} liquid names; EDGE built {str(ph.get('edge_built_at'))[:16]}; {ph.get('n_problems', 0)} problems")
        for p in ph.get("problems", []):
            amber.append(f"ph {p.get('check')}: {p.get('detail')}")
        if ph.get("error"):
            amber.append(f"ph check could not run: {ph['error']}")

    rp = f.get("rate_path")
    if rp:
        lines.append(f"ratepath  {rp.get('n_meetings_priced')} FOMC meetings priced, {rp.get('n_sr3_quoted')} SOFR futures quoted, "
                     f"{rp.get('history_days')} days of snapshots, {rp.get('n_problems', 0)} problems")
        for p in rp.get("problems", []):
            amber.append(f"rate path {p.get('check')}: {p.get('detail')}")
        if rp.get("error"):
            amber.append(f"rate path check could not run: {rp['error']}")

    extra_sections(f, lines, amber)

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

    # Catch-all: the gate's total must be explained. A section added to freshness.py later and not read here shows up as this line.
    if (f.get("n_problems") or 0) > 0 and not red and not amber:
        nz = [k for k, v in f.items() if isinstance(v, dict) and (v.get("n_problems") or v.get("n_never_loaded"))]
        amber.append(f"freshness reports {f['n_problems']} problems that health does not explain: {', '.join(nz) or 'unknown section'}")

    verdict = "RED" if red else "AMBER" if amber else "GREEN"
    if as_json:
        print(json.dumps({"verdict": verdict, "red": red, "amber": amber, "as_of": f.get("as_of")}))
        return 0 if verdict == "GREEN" else 1
    try:
        import datetime as _dt, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import events as EV
        day = _dt.date.today().isoformat()
        EV.log_event("cgi_health", "verdict", "info", f"CGI health {verdict}", key=f"cgi_health-verdict:{verdict}:{day}")
        for tag, level, items in (("red", "alert", red), ("amber", "warn", amber)):
            for i in items:
                EV.log_event("cgi_health", tag, level, i, key=f"cgi_health-{tag}:{i[:80]}:{day}")
    except Exception:  # noqa: BLE001 -- the event log is optional
        pass
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

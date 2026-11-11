"""
positions_calendar.py -- one morning screen: for every live Pulse plan, what lands on that name in the next 21 days,
plus the macro releases that move its book. Plain text, zero tokens, read-only.

Reads:  ~/pulse/mirror/plans and results (the Pulse mirror; never written)
        the public CGI site only (no token, no AWS): /api/freshness (next FOMC/CPI/GDP/SLOOS) and /api/ph
        (PH expected report dates, filings, BSP and PH macro calendar)
Writes: ~/market-dashboard/out/positions_calendar.txt (and prints it). Nothing else.

Live plan = status "planned", or a results doc with a fill and no exit (open). A US fill made at IBKR is not in the mirror,
so a filled US plan still reads "planned". US single-name earnings are "not checked": no free no-token source exists.

Run:  python3 scripts/positions_calendar.py [--notify] [--mirror DIR] [--out FILE] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events as EV  # noqa: E402

HORIZON = 21           # days: company events and own exit dates
SOON_TRADING_DAYS = 3  # "!" marker
RECENT_DAYS = 2        # "filed in the last 2 days"
MIRROR_STALE_H = 26
CAL_STALE_DAYS = 8

AXIS = {"FOMC": "liquidity", "SLOOS": "credit", "CPI": "inflation", "GDP": "growth"}
RESULT_WORDS = ("result", "net income", "financial and operating", "earnings")


def fetch_json(path: str):
    from cgi_health import _get  # same public-site helper as the health check
    return json.loads(_get(path))


# ── pure helpers (tested in check_positions_calendar.py) ─────────────────────────────────────────

def trading_days_away(today: dt.date, d: dt.date) -> int:
    """Weekdays from today (exclusive) to d (inclusive); negative if d is past. Holidays are not modelled."""
    if d == today:
        return 0
    step = 1 if d > today else -1
    n, x = 0, today
    while x != d:
        x += dt.timedelta(days=step)
        if x.weekday() < 5:
            n += step
    return n


def live_plans(plans: list[dict], results: dict[str, dict]) -> list[dict]:
    """planned, or open (a fill and no exit). Closed (exit set), not triggered, expired and passed are dropped."""
    out = []
    for p in plans:
        r = results.get(p.get("id"))
        has_fill = bool(r) and r.get("fill") is not None
        if has_fill and r.get("exit") is not None:
            continue
        if has_fill:
            out.append({**p, "_state": "open", "_fill": r["fill"], "_fill_ts": r.get("ts")})
        elif p.get("status") == "planned":
            out.append({**p, "_state": "planned"})
    return out


def plan_exit(p: dict, today: dt.date):
    """The plan's own exit_by field (set on the Pulse plan form), with its optional exit_reason.
    Returns (date, reason) or None. Dates in free text are not guessed."""
    xb = p.get("exit_by")
    if xb and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(xb)):
        try:
            return dt.date.fromisoformat(xb), (str(p.get("exit_reason") or "").strip() or None)
        except ValueError:
            pass
    return None


def macro_rows(book: str, today: dt.date, fresh: dict | None, ph: dict | None) -> list[dict]:
    """Next release per type for the book's macro (any distance: they are the ones that move it)."""
    rows = []
    if book == "US":
        nxt = ((fresh or {}).get("release_calendar") or {}).get("next") or {}
        for t in ("CPI", "GDP", "FOMC", "SLOOS"):
            if nxt.get(t):
                rows.append({"date": dt.date.fromisoformat(nxt[t]), "label": f"{t} (can flip {AXIS[t]})"})
    elif book == "PH":
        bsp = (((ph or {}).get("header") or {}).get("bsp") or {}).get("next")
        if bsp:
            rows.append({"date": dt.date.fromisoformat(bsp), "label": "BSP decision"})
        seen = set()
        for e in ((ph or {}).get("calendar") or {}).get("events", []):
            if e.get("kind") in ("cpi", "gdp") and e.get("date") and dt.date.fromisoformat(e["date"]) >= today:
                key = (e["date"], e["kind"])
                if key not in seen:
                    seen.add(key)
                    rows.append({"date": dt.date.fromisoformat(e["date"]),
                                 "label": f"PH {'CPI' if e['kind'] == 'cpi' else 'GDP'} {e.get('period') or ''}".strip()})
    rows = [r for r in rows if r["date"] >= today]
    rows.sort(key=lambda r: r["date"])
    return rows


def company_events(sym: str, today: dt.date, ph: dict | None) -> tuple[list[dict], bool]:
    """PH only. Returns (events, results_out). Events: filed in the last 2 days, then expected report dates within 21 days."""
    ev, results_out = [], False
    if not ph:
        return ev, results_out
    cutoff = today - dt.timedelta(days=RECENT_DAYS)

    def when(ts: str) -> dt.date:
        return dt.date.fromisoformat(ts[:10])

    for r in (ph.get("earnings") or {}).get("reported", []):
        if r.get("s") == sym and when(r["announced"]) >= cutoff:
            yoy = f", net income {r['ni_yoy']:+.0f}% y/y" if r.get("ni_yoy") is not None else ""
            form = {"Q": "17-Q", "A": "17-A"}.get(r.get("kind"), r.get("kind", ""))
            ev.append({"date": when(r["announced"]), "kind": "filed", "label": f"{form} for period {r.get('period', '')} filed{yoy} (quarter)" if r.get("kind") == "Q"
                       else f"{form} for period {r.get('period', '')} filed{yoy}"})
            results_out = True
    seen_titles = set()
    for d in ph.get("material", []):
        if d.get("symbol") != sym or when(d["announced"]) < cutoff:
            continue
        title = (d.get("subject") or d.get("title") or "").strip()
        low = title.lower()
        if d.get("chip") == "press" and not any(w in low for w in RESULT_WORDS):
            continue                                   # a bare "Press Release" tells nothing
        if title in seen_titles:
            continue
        seen_titles.add(title)
        if any(w in low for w in RESULT_WORDS):
            results_out = True
        ev.append({"date": when(d["announced"]), "kind": "filed", "label": f"{d.get('chip', '')}: {title[:70]}"})
    for n in (ph.get("earnings") or {}).get("next", []):
        if n.get("s") == sym:
            d = dt.date.fromisoformat(n["date"])
            if 0 <= (d - today).days <= HORIZON:
                note = " (press release already out)" if results_out else ""
                ev.append({"date": d, "kind": "results", "label": f"17-Q/results expected ({n.get('basis', 'estimate')}){note}"})
    ev.sort(key=lambda e: (e["date"], e["kind"]))
    return ev, results_out


# ── open risk (handoff 01) ────────────────────────────────────────────────────────────────────────
ONE_R = {"PH": 25000.0, "US": 600.0}   # PHP / USD; 1R = 0.25% of the book, so percent = R x 0.25 (no balances read)
PCT_PER_R = 0.25
RISK_CAP = 1.50                          # his own open-risk cap, % per book
RISK_NEAR = 1.40                         # "at cap" band starts here (default; adjustable)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def plan_risk(p: dict) -> dict:
    """Risk of one live plan in R. Open: (fill - stop) x shares. Planned: (entry - stop) x shares. Never negative.
    unknown=True when stop, shares (or the fill of an open trade) is missing or not a positive number."""
    book = p.get("book") or "US"
    stop, shares = _num(p.get("stop")), _num(p.get("shares"))
    ref = _num(p.get("_fill")) if p.get("_state") == "open" else _num(p.get("entry"))
    if stop is None or shares is None or shares <= 0 or ref is None:
        return {"symbol": p.get("symbol", "?"), "state": p.get("_state"), "unknown": True, "r": None, "locked": False}
    risk = (ref - stop) * shares
    return {"symbol": p.get("symbol", "?"), "state": p.get("_state"), "unknown": False,
            "r": max(0.0, risk) / ONE_R.get(book, ONE_R["US"]), "locked": risk <= 0}


def cap_status(pct: float) -> str:
    pct = round(pct, 2)
    return "OVER" if pct > RISK_CAP else "AT CAP" if pct >= RISK_NEAR else "under"


def open_risk(live: list[dict]) -> dict:
    """Per book: live (open) and if-all-trigger (open + planned) risk in R and %, unknown names, cap status."""
    out = {}
    for book in ("PH", "US"):
        items = [plan_risk(p) for p in live if (p.get("book") or "US") == book]
        live_r = sum(i["r"] for i in items if i["state"] == "open" and not i["unknown"])
        plan_r = sum(i["r"] for i in items if i["state"] == "planned" and not i["unknown"])
        unknown = [i["symbol"] for i in items if i["unknown"]]
        out[book] = {"items": items, "unknown": unknown, "live_r": live_r, "all_r": live_r + plan_r,
                     "live_pct": round(live_r * PCT_PER_R, 2), "all_pct": round((live_r + plan_r) * PCT_PER_R, 2),
                     "status": cap_status((live_r + plan_r) * PCT_PER_R), "live_status": cap_status(live_r * PCT_PER_R)}
    return out


def risk_lines(live: list[dict]) -> list[str]:
    rk = open_risk(live)
    over = [b for b in ("PH", "US") if rk[b]["status"] == "OVER" or rk[b]["live_status"] == "OVER"]
    L: list[str] = []

    def seg(b):
        r = rk[b]
        inc = f" INCOMPLETE ({len(r['unknown'])} unknown)" if r["unknown"] else ""
        atl = "at least " if r["unknown"] else ""
        return (f"{b} live {atl}{r['live_pct']:.2f}% ({r['live_r']:.1f}R) | if all trigger {atl}{r['all_pct']:.2f}% ({r['all_r']:.1f}R) "
                f"{'cap not confirmed' if r['unknown'] and r['status'] != 'OVER' else r['status']}{inc}")
    comb = rk["PH"]["all_pct"] + rk["US"]["all_pct"]
    inc_any = bool(rk["PH"]["unknown"] or rk["US"]["unknown"])
    L.append("RISK  " + "    ".join(seg(b) for b in ("PH", "US")) +
             f"    combined {'at least ' if inc_any else ''}{comb:.2f}% if all trigger (for information, no cap)")
    for b in ("PH", "US"):
        r = rk[b]
        def fmt(i):
            if i["unknown"]:
                return f"{i['symbol']} risk unknown"
            return f"{i['symbol']} {i['r']:.2f}R" + (" (stop at or above fill: 0R)" if i["locked"] and i["state"] == "open" else "")
        o = [fmt(i) for i in r["items"] if i["state"] == "open"]
        pl = [fmt(i) for i in r["items"] if i["state"] == "planned"]
        if not (o or pl):
            continue
        parts = []
        if o:
            parts.append(f"{b} open: " + "  ".join(o) + "  (partials not seen)")
        if pl:
            parts.append(f"{b} planned: " + "  ".join(pl))
        L.append("      " + " | ".join(parts))
    L.append("      Cap " + f"{RISK_CAP:.2f}% per book, 1R = {PCT_PER_R}% of the book. Small sample, a guide. "
             "A US fill at IBKR is not in the mirror, so a filled US plan counts as planned. A stop moved only at the broker is not seen.")
    if over:
        L.insert(0, "!! OPEN RISK OVER CAP: " + "; ".join(
            f"{b} {'live' if rk[b]['live_status'] == 'OVER' else 'if all trigger'} "
            f"{rk[b]['live_pct' if rk[b]['live_status'] == 'OVER' else 'all_pct']:.2f}% (cap {RISK_CAP:.2f}%)" for b in over))
    return L


def rel_day(today: dt.date, d: dt.date) -> str:
    n = (d - today).days
    return "today" if n == 0 else "yesterday" if n == -1 else f"{n:+d}d" if n < 0 else f"+{n}d"


def stale_items(meta: dict, fresh: dict | None, ph: dict | None, errors: list[str], now: dt.datetime, today: dt.date) -> list[tuple]:
    """Header problems as (source, kind, level, text): a stale mirror, bad CGI data, an unreadable source."""
    out = []
    sa = meta.get("synced_at")
    if sa:
        age_h = (now - dt.datetime.fromisoformat(sa.replace("Z", "+00:00"))).total_seconds() / 3600
        if age_h > MIRROR_STALE_H:
            out.append(("mirror", "stale", "alert", f"PULSE MIRROR STALE: synced {sa} ({age_h:.0f} h ago); plans below may be out of date"))
    else:
        out.append(("mirror", "no_sync", "alert", "PULSE MIRROR: no synced_at found"))
    if fresh is None:
        out.append(("cgi", "unreadable", "alert", "CGI freshness unreadable: US release dates missing"))
    elif fresh.get("n_problems"):
        where = [k for k, v in fresh.items() if isinstance(v, dict) and (v.get("n_problems") or 0) > 0]
        out.append(("cgi", "freshness", "warn", f"CGI freshness reports {fresh['n_problems']} data problem(s)" + (f" in: {', '.join(where)}" if where else "")))
    if ph is None:
        out.append(("calendar", "ph_unreadable", "warn", "CGI /api/ph unreadable: PH events missing"))
    else:
        cal_asof = ((ph.get("calendar") or {}).get("asof") or "")[:10]
        if cal_asof and (today - dt.date.fromisoformat(cal_asof)).days > CAL_STALE_DAYS:
            out.append(("calendar", "ph_calendar_old", "warn", f"PH macro calendar last refreshed {cal_asof} ({(today - dt.date.fromisoformat(cal_asof)).days} days ago)"))
        age = (((ph.get("freshness") or {}).get("edge") or {}).get("age_days"))
        if age is not None and age > 2:
            out.append(("calendar", "edge_old", "warn", f"PSE EDGE snapshot is {age} days old"))
    for e in errors:
        out.append(("calendar", "source_error", "warn", e))
    return out


def past_exit_items(live: list[dict], today: dt.date) -> list[tuple]:
    """(symbol, state, exit_date, reason) for live plans past their exit_by date."""
    out = []
    for p in sorted(live, key=lambda q: q["symbol"]):
        ex = plan_exit(p, today)
        if ex and ex[0] < today:
            out.append((p["symbol"], p["_state"], ex[0], ex[1]))
    return out


def build(today: dt.date, plans: list[dict], results: dict, meta: dict, fresh: dict | None, ph: dict | None,
          errors: list[str], now: dt.datetime | None = None, events: list[dict] | None = None,
          stress: list[str] | None = None) -> tuple[str, int]:
    """Returns (text, n_soon). With events (screen-ordered), a SINCE YESTERDAY block leads and the old lead/stale lines fold into it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    L: list[str] = []
    sa = meta.get("synced_at")
    stale = [t for _, _, _, t in stale_items(meta, fresh, ph, errors, now, today)]

    live = live_plans(plans, results)
    rl = risk_lines(live)
    past = [f"!! PAST EXIT DATE: {sym} ({state}) exit by {d.isoformat()}, {(today - d).days}d ago" + (f" ({reason})" if reason else "")
            for sym, state, d, reason in past_exit_items(live, today)]
    if events is not None:
        if rl and rl[0].startswith("!!"):
            rl.pop(0)
        na, nw = sum(e["level"] == "alert" for e in events), sum(e["level"] == "warn" for e in events)
        L.append(f"SINCE YESTERDAY: {na} alert(s), {nw} warning(s)" if events else "SINCE YESTERDAY: nothing to flag")
        for e in events:
            L.append(f"  {'!!' if e['level'] == 'alert' else ' ~'} {e['text']}  [{e['source']}]")
        L.append("")
        stale = []
    else:
        if rl and rl[0].startswith("!!"):
            L.append(rl.pop(0))
        L.extend(past)
    L.append(f"POSITIONS CALENDAR  {today.isoformat()} ({today.strftime('%a')})   window {HORIZON} days   ! = within {SOON_TRADING_DAYS} trading days")
    L.append(f"mirror synced_at {sa or 'unknown'} | CGI freshness as of {(fresh or {}).get('as_of', 'unknown')} "
             f"({(fresh or {}).get('n_problems', '?')} problems) | PH data as of {(ph or {}).get('as_of', 'unknown')}")
    for s in stale:
        L.append(f"  ~ {s}")
    L.extend(rl)
    if stress:
        L.extend(stress)
    L.append("")

    n_soon = 0
    for book in ("PH", "US"):
        group = sorted([p for p in live if (p.get("book") or "US") == book], key=lambda p: p["symbol"])
        if not group:
            L.append(f"{book}: no live plans")
            L.append("")
            continue
        L.append(f"{book}  ({len(group)} live)")
        macro = macro_rows(book, today, fresh, ph)
        for p in group:
            sym = p["symbol"]
            state = f"open, filled {p['_fill']}" + (f" on {p['_fill_ts'][:10]}" if p.get("_fill_ts") else "") if p["_state"] == "open" \
                else f"planned {p.get('plan_ts', '')[:10]}, trigger {p.get('entry')} stop {p.get('stop')}"
            rk = plan_risk(p)
            state += "  | risk unknown" if rk["unknown"] else f"  | risk {rk['r']:.2f}R"
            L.append(f"  {sym:<6} {state}")
            items: list[tuple[dt.date, str, str]] = []
            if book == "PH":
                evs, _ = company_events(sym, today, ph)
                items += [(e["date"], e["kind"], e["label"]) for e in evs]
            ex = plan_exit(p, today)
            if p.get("wrong"):
                L.append(f"      wrong: {p['wrong']}")
            if ex and 0 <= (ex[0] - today).days <= HORIZON:
                items.append((ex[0], "own exit", "exit by" + (f": {ex[1]}" if ex[1] else "")))
            items.sort(key=lambda i: i[0])
            for d, kind, label in items:
                soon = 0 <= trading_days_away(today, d) <= SOON_TRADING_DAYS and d >= today
                n_soon += 1 if soon else 0
                L.append(f"    {'!' if soon else ' '} {d.isoformat()} {rel_day(today, d):>9}  {kind:<8} {label}")
            if not items:
                L.append("      nothing scheduled for this name in 21 days" + (" (ETF: macro only; US earnings: not checked)" if book == "US" else ""))
            elif book == "US":
                L.append("      US earnings: not checked")
            if not ex:
                L.append("      exit date: not stated")
            elif not (0 <= (ex[0] - today).days <= HORIZON):
                L.append(f"      exit by {ex[0].isoformat()} ({rel_day(today, ex[0])})" + (f": {ex[1]}" if ex[1] else "") + ("  PAST EXIT DATE" if ex[0] < today else ""))
            for m in macro:
                soon = trading_days_away(today, m["date"]) <= SOON_TRADING_DAYS
                n_soon += 1 if soon else 0
                L.append(f"    {'!' if soon else ' '} {m['date'].isoformat()} {rel_day(today, m['date']):>9}  macro    {m['label']}")
        L.append("")
    L.append("Notes: weekdays only, no holiday calendar. US single-name earnings not checked (no free source). PH expected dates are "
             "'last year +365 days' estimates. A US fill at IBKR is not in the mirror, so a filled US plan still reads planned.")
    n_soon += len(past)
    n_soon += sum(1 for b, r in open_risk(live).items() if r["status"] == "OVER" or r["live_status"] == "OVER")
    return "\n".join(L) + "\n", n_soon


def calendar_events(today: dt.date, now: dt.datetime, live: list[dict], meta: dict, fresh: dict | None, ph: dict | None,
                    errors: list[str]) -> list[dict]:
    """Everything this run knows that belongs in the event log. Conditions that persist carry today's date in their key,
    so they reappear each morning until fixed. No Flow text, no plan wording, no balances."""
    ev = []

    def add(source, kind, level, text, key, symbol=None):
        ev.append({"source": source, "kind": kind, "level": level, "text": text, "symbol": symbol, "key": f"{key}:{today.isoformat()}"})

    sa = meta.get("synced_at")
    if sa:
        ev.append({"source": "mirror", "kind": "synced", "level": "info", "text": f"Pulse mirror synced {sa}", "symbol": None, "key": f"mirror-synced:{sa}"})
    for source, kind, level, text in stale_items(meta, fresh, ph, errors, now, today):
        if source == "mirror" and kind == "stale" and today.weekday() >= 5:
            continue                                       # stale on a weekday only
        add(source, kind, level, text, f"{source}-{kind}")
    rk = open_risk(live)
    for b, r in rk.items():
        if "OVER" in (r["status"], r["live_status"]):
            live_over = r["live_status"] == "OVER"
            add("calendar", "risk_over", "alert",
                f"{b} open risk {r['live_pct' if live_over else 'all_pct']:.2f}% {'live' if live_over else 'if all trigger'}: OVER the {RISK_CAP:.2f}% cap", f"risk-over-{b}")
        if r["unknown"]:
            add("calendar", "risk_unknown", "warn", f"{b} open risk incomplete: risk unknown for {', '.join(r['unknown'])}", f"risk-unknown-{b}")
    for sym, state, d, reason in past_exit_items(live, today):
        add("calendar", "past_exit", "alert", f"PAST EXIT DATE: {sym} ({state}) exit by {d.isoformat()}, {(today - d).days}d ago"
            + (f" ({reason})" if reason else ""), f"past-exit-{sym}", sym)
    for book in ("PH", "US"):
        group = [p for p in live if (p.get("book") or "US") == book]
        if not group:
            continue
        for m in macro_rows(book, today, fresh, ph):
            if trading_days_away(today, m["date"]) <= SOON_TRADING_DAYS:
                add("calendar", "release", "warn", f"{m['label']} {m['date'].isoformat()} ({rel_day(today, m['date'])}) bears on the {book} book",
                    f"release-{book}-{m['label']}-{m['date'].isoformat()}")
        for p in group:
            sym = p["symbol"]
            ex = plan_exit(p, today)
            if ex and 0 <= trading_days_away(today, ex[0]) <= SOON_TRADING_DAYS and ex[0] >= today:
                add("calendar", "exit_soon", "warn", f"{sym} exit by {ex[0].isoformat()} ({rel_day(today, ex[0])})" + (f": {ex[1]}" if ex[1] else ""), f"exit-soon-{sym}", sym)
            if book == "PH":
                evs, results_out = company_events(sym, today, ph)
                if results_out:
                    add("calendar", "results_filed", "warn", f"{sym} results filed in the last {RECENT_DAYS} days (see EDGE)", f"results-filed-{sym}", sym)
                for e in evs:
                    if e["kind"] == "results" and 0 <= trading_days_away(today, e["date"]) <= SOON_TRADING_DAYS:
                        add("calendar", "results_expected", "warn", f"{sym} results expected {e['date'].isoformat()} ({rel_day(today, e['date'])})", f"results-expected-{sym}", sym)
    return ev


def regime_event(code: str | None, state_path: str, today: dt.date) -> dict | None:
    """Compare today's regime code with the last one seen. The first run only records a baseline."""
    if not code:
        return None
    try:
        prev = json.load(open(state_path)).get("regime")
    except (OSError, ValueError):
        prev = None
    try:
        json.dump({"regime": code, "seen": today.isoformat()}, open(state_path, "w"))
    except OSError:
        pass
    if prev and prev != code:
        return {"source": "regime", "kind": "changed", "level": "alert", "text": f"Regime changed: {prev} to {code}", "symbol": None,
                "key": f"regime-changed:{prev}>{code}"}
    return None


def current_regime() -> str | None:
    try:
        b = fetch_json("/api/brief?cadence=daily")
        h = next((x.get("headline") for x in b.get("sections", []) if x.get("name") == "regime"), "") or ""
        m = re.match(r"\s*(C\d\s*G\d)", h)
        return m.group(1).replace(" ", "") if m else None
    except Exception:  # noqa: BLE001
        return None


def load_mirror(mirror: str):
    plans = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(mirror, "plans", "*.json")))]
    results = {}
    for f in glob.glob(os.path.join(mirror, "results", "*.json")):
        r = json.load(open(f))
        results[r.get("plan") or os.path.basename(f)[:-5]] = r
    try:
        meta = json.load(open(os.path.join(mirror, "_meta.json")))
    except Exception:  # noqa: BLE001
        meta = {}
    return plans, results, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default=os.path.expanduser("~/pulse/mirror"))
    ap.add_argument("--out", default=os.path.expanduser("~/market-dashboard/out/positions_calendar.txt"))
    ap.add_argument("--today", default=None)
    ap.add_argument("--notify", action="store_true", help="macOS notification only when a '!' item exists")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    errors: list[str] = []
    plans, results, meta = load_mirror(a.mirror)
    fresh = ph = None
    try:
        fresh = fetch_json("/api/freshness")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"could not read /api/freshness: {exc}")
    try:
        ph = fetch_json("/api/ph")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"could not read /api/ph: {exc}")
    now = dt.datetime.now(dt.timezone.utc)
    live = live_plans(plans, results)
    mem = calendar_events(today, now, live, meta, fresh, ph, errors)
    try:   # no-token trigger for the release-day read (handoff 07); the read itself is a gated Claude run
        import release_trigger
        mem.extend(release_trigger.run(today, live, fresh, ph))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"release trigger not run: {exc}")
    rg = regime_event(current_regime(), os.path.join(os.path.dirname(EV.path()), "events_state.json"), today)
    if rg:
        mem.append(rg)
    n_new_alerts, first_alert = 0, None
    for e in mem:
        if EV.log_event(e["source"], e["kind"], e["level"], e["text"], e.get("symbol"), e["key"], file=None) and e["level"] == "alert":
            n_new_alerts += 1
            first_alert = first_alert or e["text"]
    EV.rotate()
    logged = EV.read_events(now - dt.timedelta(hours=24))
    seen = {e.get("key") for e in logged}
    merged = logged + [dict(e, ts=now.strftime("%Y-%m-%dT%H:%M:%SZ")) for e in mem if e["key"] not in seen]   # if the log could not be written
    stress = []
    try:   # gap-shock lines (handoff 10): a failure here shows as one line, never breaks the calendar
        import gap_shock
        import watch_table
        try:
            themes = fetch_json("/api/themes")
        except Exception:  # noqa: BLE001
            themes = None
        stress = gap_shock.stress_lines(gap_shock.compute(live, open_risk(live), ph, {k: v[0] for k, v in watch_table.theme_map(themes).items()}))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"gap shock not computed: {exc}")
    text, n_soon = build(today, plans, results, meta, fresh, ph, errors, now, events=EV.screen_order(merged), stress=stress)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(text)
    print(text, end="")
    try:   # no-token trigger for the thesis fact-check run (handoff 06); queues ids only, the run itself is gated
        import thesis_trigger
        thesis_trigger.run(a.mirror, today=today, log=True)
    except Exception as exc:  # noqa: BLE001
        print(f"(thesis trigger not run: {exc})")
    try:   # the watch table runs as a second step of this job (no separate schedule); a failure here never breaks the calendar
        import watch_table
        watch_table.main(["--mirror", a.mirror], echo=False)
    except Exception as exc:  # noqa: BLE001
        print(f"(watch table not written: {exc})")
    if a.notify and (n_soon or n_new_alerts):
        msg = (f"ALERT: {first_alert[:100]}" if first_alert else f"{n_soon} flag(s) (events within {SOON_TRADING_DAYS} trading days, or open risk over cap)") + ": see out/positions_calendar.txt"
        subprocess.run(["osascript", "-e", f'display notification "{msg.replace(chr(34), "")}" with title "Positions calendar"'], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

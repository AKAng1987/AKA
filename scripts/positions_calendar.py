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

HORIZON = 21           # days: company events and own exit dates
SOON_TRADING_DAYS = 3  # "!" marker
RECENT_DAYS = 2        # "filed in the last 2 days"
MIRROR_STALE_H = 26
CAL_STALE_DAYS = 8

AXIS = {"FOMC": "liquidity", "SLOOS": "credit", "CPI": "inflation", "GDP": "growth"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
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


def exit_from_text(text: str, today: dt.date):
    """First explicit date in free text: ISO, '20 Nov', 'Nov 20'. Returns (date, snippet) or None. A month-day already more
    than 30 days past rolls to next year."""
    if not text:
        return None
    cands = []
    for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text):
        try:
            cands.append((m.start(), dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m))
        except ValueError:
            pass
    mon = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{mon}\b", text, re.I):
        cands.append((m.start(), (int(m.group(1)), MONTHS[m.group(2).lower()[:3]]), m))
    for m in re.finditer(rf"\b{mon}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text, re.I):
        cands.append((m.start(), (int(m.group(2)), MONTHS[m.group(1).lower()[:3]]), m))
    cands.sort(key=lambda c: c[0])
    for _, v, m in cands:
        if isinstance(v, tuple):
            day, mo = v
            try:
                d = dt.date(today.year, mo, day)
            except ValueError:
                continue
            if (today - d).days > 30:
                d = dt.date(today.year + 1, mo, day)
        else:
            d = v
        a, b = max(0, m.start() - 25), min(len(text), m.end() + 25)
        return d, text[a:b].strip().replace("\n", " ")
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


def rel_day(today: dt.date, d: dt.date) -> str:
    n = (d - today).days
    return "today" if n == 0 else "yesterday" if n == -1 else f"{n:+d}d" if n < 0 else f"+{n}d"


def build(today: dt.date, plans: list[dict], results: dict, meta: dict, fresh: dict | None, ph: dict | None,
          errors: list[str], now: dt.datetime | None = None) -> tuple[str, int]:
    """Returns (text, n_soon)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    L: list[str] = []
    stale = []
    sa = meta.get("synced_at")
    if sa:
        age_h = (now - dt.datetime.fromisoformat(sa.replace("Z", "+00:00"))).total_seconds() / 3600
        if age_h > MIRROR_STALE_H:
            stale.append(f"PULSE MIRROR STALE: synced {sa} ({age_h:.0f} h ago); plans below may be out of date")
    else:
        stale.append("PULSE MIRROR: no synced_at found")
    if fresh is None:
        stale.append("CGI freshness unreadable: US release dates missing")
    elif fresh.get("n_problems"):
        where = [k for k, v in fresh.items() if isinstance(v, dict) and (v.get("n_problems") or 0) > 0]
        stale.append(f"CGI freshness reports {fresh['n_problems']} data problem(s)" + (f" in: {', '.join(where)}" if where else ""))
    if ph is None:
        stale.append("CGI /api/ph unreadable: PH events missing")
    else:
        cal_asof = ((ph.get("calendar") or {}).get("asof") or "")[:10]
        if cal_asof and (today - dt.date.fromisoformat(cal_asof)).days > CAL_STALE_DAYS:
            stale.append(f"PH macro calendar last refreshed {cal_asof} ({(today - dt.date.fromisoformat(cal_asof)).days} days ago)")
        age = (((ph.get("freshness") or {}).get("edge") or {}).get("age_days"))
        if age is not None and age > 2:
            stale.append(f"PSE EDGE snapshot is {age} days old")
    stale += errors

    L.append(f"POSITIONS CALENDAR  {today.isoformat()} ({today.strftime('%a')})   window {HORIZON} days   ! = within {SOON_TRADING_DAYS} trading days")
    L.append(f"mirror synced_at {sa or 'unknown'} | CGI freshness as of {(fresh or {}).get('as_of', 'unknown')} "
             f"({(fresh or {}).get('n_problems', '?')} problems) | PH data as of {(ph or {}).get('as_of', 'unknown')}")
    for s in stale:
        L.append(f"  ~ {s}")
    L.append("")

    live = live_plans(plans, results)
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
            L.append(f"  {sym:<6} {state}")
            items: list[tuple[dt.date, str, str]] = []
            if book == "PH":
                evs, _ = company_events(sym, today, ph)
                items += [(e["date"], e["kind"], e["label"]) for e in evs]
            ex = exit_from_text(" ".join(str(p.get(k) or "") for k in ("wrong", "feeling")), today)
            if ex and 0 <= (ex[0] - today).days <= HORIZON:
                items.append((ex[0], "own exit", f"exit, from your text: \"{ex[1]}\""))
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
                L.append(f"      exit, from your text: {ex[0].isoformat()} ({rel_day(today, ex[0])}), outside the window")
            for m in macro:
                soon = trading_days_away(today, m["date"]) <= SOON_TRADING_DAYS
                n_soon += 1 if soon else 0
                L.append(f"    {'!' if soon else ' '} {m['date'].isoformat()} {rel_day(today, m['date']):>9}  macro    {m['label']}")
        L.append("")
    L.append("Notes: weekdays only, no holiday calendar. US single-name earnings not checked (no free source). PH expected dates are "
             "'last year +365 days' estimates. A US fill at IBKR is not in the mirror, so a filled US plan still reads planned.")
    return "\n".join(L) + "\n", n_soon


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
    text, n_soon = build(today, plans, results, meta, fresh, ph, errors if (fresh is not None and ph is not None) else errors)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(text)
    print(text, end="")
    if a.notify and n_soon:
        msg = f"{n_soon} item(s) within {SOON_TRADING_DAYS} trading days: see out/positions_calendar.txt"
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Positions calendar"'], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

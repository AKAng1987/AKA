"""Build the PH price snapshot (returns, trend, liquidity, float, market cap, PSEi) for the CGI Philippines tab, from EDGE's
own daily bars (the chart on each company's Stock Data page) plus the PSEi from Yahoo. Zero tokens: runs unattended.

Run from ~/cgi-vercel with its venv (storage lives in api/ph_data.py):

    api/.venv/bin/python ~/market-dashboard/scripts/ph_prices_build.py --out /tmp/prices.json            # daily set, local file only
    api/.venv/bin/python ~/market-dashboard/scripts/ph_prices_build.py --upload                          # daily: build and store
    api/.venv/bin/python ~/market-dashboard/scripts/ph_prices_build.py --full --upload                   # every listed company (first run, weekly)

One chart request per company (two seconds apart): the daily set is the names that traded at least PHP 1M a day on average
at the last full run, about 5 minutes; --full is all 284 companies, about 10 minutes. Float and outstanding shares come from the
Stock Data page of the liquid names, refreshed weekly. Keeps numbers only, never a copy of an EDGE page (EDGE terms).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
import ph_edge as E  # noqa: E402

HISTORY_DAYS = 560           # EMA200 and a year-to-date base need about 14 months of sessions
LIQ_KEEP = 1_000_000         # rows kept in the snapshot (the page shows >= 5M; the floor lets a name enter the list)
LIQ_PAGE = 5_000_000
BARS_KEEP = 300              # sessions of daily bars kept for the page's chart dock (EMA200 needs the warm-up)
# Names Arvin asked for by name: always in the snapshot and on the page, whatever they trade (COL Financial, CTS Global Equity).
PINNED = ("COL", "CTS")
STOCK_PAGE_DAYS = 7
a_notify = False

# PSEi members as of 2026-11-01 (TradingView index set SYML:PSE;PSEI, read 2026-11-01). PSE reviews the list twice a year
# (February and August), so this is updated by hand after each review; the rebalance screen on the page exists to see it coming.
PSEI_MEMBERS = ("ICT SM BDO BPI SMPH MER AC MBT TEL EMI GLO ALI AEV LTG JFC SMC AREIT CBC MYNLD URC RCR JGS PGOLD MONDE ACEN CNPF GTCAP DMC SCC PLUS").split()


def ema(values: list[float], n: int) -> float | None:
    if len(values) < max(5, n // 3):
        return None
    k = 2.0 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def months_back(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    import calendar

    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def calendar_returns(dates: list[str], closes: list[float]) -> dict:
    """1W / 1M / 3M / YTD the way TradingView counts them: against the last close on or before the same calendar day a week, a
    month, three months earlier, and the last close of the previous year (not N sessions back, which drifts around holidays)."""
    if not dates:
        return {}
    last = dt.date.fromisoformat(dates[-1])
    c = closes[-1]

    def base(target: dt.date, strict: bool = False):
        iso = target.isoformat()
        for d, v in zip(reversed(dates), reversed(closes)):
            if (d < iso) if strict else (d <= iso):
                return v
        return None

    def ret(b):
        return round((c / b - 1) * 100, 2) if b else None
    return {"w": ret(base(last - dt.timedelta(days=7))), "m": ret(base(months_back(last, 1))), "q": ret(base(months_back(last, 3))),
            "y": ret(base(dt.date(last.year, 1, 1), strict=True))}


def metrics(bars: list[dict], today: dt.date, max_stale: int = 10) -> dict | None:
    """Numbers from one company's daily bars (oldest first). None when the stock has not traded for 10 days (suspended)."""
    if not bars:
        return None
    last = bars[-1]
    if (today - dt.date.fromisoformat(last["date"])).days > max_stale:
        return None
    dates = [b["date"] for b in bars]
    closes = [b["close"] for b in bars]
    c = closes[-1]
    e50, e200 = ema(closes, 50), ema(closes, 200)
    v30 = sum(b["value"] for b in bars[-30:]) / min(30, len(bars))
    last63 = bars[-63:]
    return {"c": c, **calendar_returns(dates, closes), "vt": last["value"], "v30": round(v30, 0),
            "e50": round(e50, 4) if e50 else None, "e200": round(e200, 4) if e200 else None,
            "h3": max(b["high"] for b in last63), "l3": min(b["low"] for b in last63), "last_date": last["date"], "n_bars": len(bars)}


BREADTH_LABELS = {"PHFD": "PSE above 5d", "PHTW": "PSE above 20d", "PHFI": "PSE above 50d", "PHTH": "PSE above 200d"}


def breadth_blob(all_bars: dict[str, list[dict]], universe: list[str], built_at: str) -> dict | None:
    """PH breadth from the EDGE bars of the liquid names, same arithmetic as Pulse's phBreadthCalc (desk.html): the share of names
    above their 5/20/50/200-day averages, net new 52-week highs minus lows (a close above the highest, or below the lowest, of the prior
    251 closes), its 8 and 20 day EMAs, and the colour after three days of the same sign. Output has the shape of web/data/ph_breadth.json."""
    acc: dict[str, dict] = {}
    for sym in universe:
        b = all_bars.get(sym) or []
        c = [z["close"] for z in b]
        cs = [0.0]
        for v in c:
            cs.append(cs[-1] + v)
        for i, z in enumerate(b):
            o = acc.setdefault(z["date"], {"n": 0, "a": {5: 0, 20: 0, 50: 0, 200: 0}, "v": {5: 0, 20: 0, 50: 0, 200: 0}, "nh": 0, "nl": 0, "vh": 0})
            o["n"] += 1
            for k in (5, 20, 50, 200):
                if i >= k - 1:
                    o["v"][k] += 1
                    if c[i] > (cs[i + 1] - cs[i + 1 - k]) / k:
                        o["a"][k] += 1
            if i >= 251:
                window = c[i - 251:i]
                o["vh"] += 1
                if c[i] > max(window):
                    o["nh"] += 1
                if c[i] < min(window):
                    o["nl"] += 1
    dates = [d for d in sorted(acc) if acc[d]["n"] >= 0.8 * len(universe)][-260:]
    gauges = {k: [] for k in BREADTH_LABELS}
    series, f, s2, run = [], None, None, []
    for d in dates:
        o = acc[d]
        for nm, k in (("PHFD", 5), ("PHTW", 20), ("PHFI", 50), ("PHTH", 200)):
            if o["v"][k] >= 10:
                gauges[nm].append([d, round(100 * o["a"][k] / o["v"][k], 1)])
        if o["vh"] >= 10:
            n = o["nh"] - o["nl"]
            f = n if f is None else f + (2 / 9) * (n - f)
            s2 = n if s2 is None else s2 + (2 / 21) * (n - s2)
            run.append(n)
            r3 = run[-3:]
            col = "green" if len(r3) == 3 and all(v > 0 for v in r3) else "red" if len(r3) == 3 and all(v < 0 for v in r3) else "white"
            series.append({"date": d, "net": n, "fast": round(f, 1), "slow": round(s2, 1), "colour": col})
    if not series or not all(gauges.values()):
        return None
    series = series[-120:]
    last = series[-1]
    sign = (last["net"] > 0) - (last["net"] < 0)
    streak = 0
    for p in reversed(series):
        if ((p["net"] > 0) - (p["net"] < 0)) == sign and sign != 0:
            streak += 1
        else:
            break
    out_g = []
    for sym in ("PHFD", "PHTW", "PHFI", "PHTH"):
        pts = gauges[sym]
        v = pts[-1][1]
        pv = pts[-2][1] if len(pts) > 1 else None
        out_g.append({"symbol": sym, "label": BREADTH_LABELS[sym], "value": v, "change": None if pv is None else round(v - pv, 1),
                      "zone": "oversold" if v < 30 else "overbought" if v > 70 else "neutral", "days_below_30_of_60": sum(1 for p in pts[-60:] if p[1] < 30)})
    return {"as_of": last["date"], "built": built_at, "source": "PSE EDGE daily bars, computed by CGI", "universe": f"{len(universe)} liquid PSE names at PHP 5M a day or more",
            "streak_days": streak, "net": last["net"], "ema_fast": last["fast"], "ema_slow": last["slow"], "colour": last["colour"],
            "series": series, "gauges": out_g}


def psei_index(prev: dict | None, seed: dict | None) -> tuple[dict, dict, dict | None]:
    """PSEi: Yahoo (PSEI.PS) serves only the latest close, so the history is kept in the snapshot, seeded once from
    data_manual/psei_history_seed_*.json and extended by each run. Returns (index summary, history)."""
    hist = dict((prev or {}).get("psei_history") or {})
    hist.update((seed or {}).get("psei_history") or {})
    try:
        import yfinance as yf

        h = yf.Ticker("PSEI.PS").history(period="5d", interval="1d")["Close"].dropna()
        for d, v in zip(h.index, h):
            hist[d.date().isoformat()] = round(float(v), 2)
    except Exception as e:  # noqa: BLE001
        print("PSEi from Yahoo failed:", str(e)[:100])
    hist = dict(sorted(hist.items())[-420:])
    if not hist:
        return {}, hist
    dates = list(hist)
    return {"c": hist[dates[-1]], **calendar_returns(dates, list(hist.values())), "date": dates[-1], "n_history": len(dates)}, hist


def build(prev: dict | None, directory: dict, full: bool, seed: dict | None = None, log=print) -> tuple[dict, dict, dict | None]:
    """directory: the EDGE snapshot's {symbol: {cmpy_id, security_id, name, sector, subsector}}."""
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    prev_rows = {r["s"]: r for r in (prev or {}).get("rows", [])}
    if full or not prev_rows:
        todo = sorted(directory)
    else:
        todo = sorted({s for s, r in prev_rows.items() if (r.get("v30") or 0) >= LIQ_KEEP * 0.5 and s in directory} | {p for p in PINNED if p in directory})
    log(f"{len(todo)} companies to read ({'full' if full or not prev_rows else 'daily set'})")
    rows, failed, suspended = [], [], 0
    bars_out: dict[str, list] = {}
    full_bars: dict[str, list] = {}
    start = today - dt.timedelta(days=HISTORY_DAYS)
    for i, sym in enumerate(todo):
        d = directory[sym]
        if not d.get("security_id"):
            continue
        try:
            bars = E.fetch_chart(d["cmpy_id"], d["security_id"], start, today)
            m = metrics(bars, today, 400 if sym in PINNED else 10)       # a pinned name that trades once a fortnight still shows
        except Exception as e:  # noqa: BLE001
            failed.append({"s": sym, "why": str(e)[:80]})
            continue
        if m is None:
            suspended += 1
            continue
        if m["v30"] >= LIQ_KEEP or sym in PINNED:
            rows.append({"s": sym, **m, "ind": d.get("subsector"), "sec": d.get("sector"), "psei": 1 if sym in PSEI_MEMBERS else 0,
                         "pinned": 1 if sym in PINNED else 0})
            full_bars[sym] = bars
            bars_out[sym] = [[b["date"], b["open"], b["high"], b["low"], b["close"], round(b["value"])] for b in bars[-BARS_KEEP:]]
        if i % 25 == 24:
            log(f"  {i + 1}/{len(todo)}")
    # float and shares outstanding from the Stock Data page, liquid names only, refreshed weekly
    nstock = 0
    for r in rows:
        old = prev_rows.get(r["s"], {})
        fresh = old.get("float_at") and (today - dt.date.fromisoformat(old["float_at"])).days < STOCK_PAGE_DAYS
        if fresh or (r["v30"] < LIQ_PAGE and not r.get("pinned")):
            r.update({k: old.get(k) for k in ("fl", "outstanding", "float_at")})
        else:
            try:
                sp = E.parse_stock_page(E.get(f"/companyPage/stockData.do?cmpy_id={directory[r['s']]['cmpy_id']}"))
                r.update({"fl": sp["free_float"], "outstanding": sp["outstanding"], "float_at": today.isoformat()})
                nstock += 1
            except Exception as e:  # noqa: BLE001
                failed.append({"s": r["s"], "why": "stock page: " + str(e)[:60]})
                r.update({k: old.get(k) for k in ("fl", "outstanding", "float_at")})
        r["mc"] = round(r["c"] * r["outstanding"], 0) if r.get("outstanding") else None
        r.setdefault("fl", None)
    asof = max((r["last_date"] for r in rows), default=None)
    index, index_history = psei_index(prev, seed)
    snap = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "asof": asof, "psei_index": index, "psei_history": index_history,
            "rows": sorted(rows, key=lambda r: -(r["v30"] or 0)),
            "last_run": {"mode": "full" if full or not prev_rows else "daily", "tried": len(todo), "kept": len(rows), "suspended": suspended,
                         "stock_pages": nstock, "failed": failed[:20], "n_failed": len(failed)}}
    universe = [r["s"] for r in rows if r["v30"] >= LIQ_PAGE]
    breadth = breadth_blob(full_bars, universe, snap["built_at"]) if len(universe) >= 40 else None
    return snap, {"built_at": snap["built_at"], "asof": asof, "bars": bars_out}, breadth


def _fail(msg: str) -> int:
    print(msg)
    if a_notify:
        subprocess.run(["osascript", "-e", f'display notification "{msg[:120]}" with title "CGI PH prices"'.replace("'", "")], check=False)
    return 1


def main() -> int:
    global a_notify
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--prev", help="start from this local snapshot (prices) instead of the stored one")
    ap.add_argument("--edge", help="read the company directory from this local EDGE snapshot instead of the stored one")
    ap.add_argument("--psei-seed", help="a data_manual/psei_history_seed_*.json file (first run only)")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    a_notify = a.notify
    import ph_data

    edge = json.load(open(a.edge)) if a.edge else ph_data.read_blob("edge")
    if not edge or not edge.get("directory"):
        return _fail("no EDGE company directory yet: run ph_edge_build.py first")
    prev = json.load(open(a.prev)) if a.prev else ph_data.read_blob("screener") if a.upload else None
    try:
        # the directory file carries cmpy_id only; security_id comes from a fresh directory read when absent
        directory = edge["directory"]
        if not all(d.get("security_id") for d in directory.values()):
            print("directory lacks security ids; reading it once ...")
            for d in E.fetch_directory():
                if d["symbol"] in directory:
                    directory[d["symbol"]]["security_id"] = d["security_id"]
        snap, bars_blob, breadth = build(prev, directory, a.full, json.load(open(a.psei_seed)) if a.psei_seed else None)
    except Exception as e:  # noqa: BLE001
        return _fail(f"PH prices build failed: {e}")
    lr = snap["last_run"]
    print(f"{lr['kept']} liquid-ish names kept of {lr['tried']} tried, {lr['suspended']} not trading, {lr['n_failed']} failed; asof {snap['asof']}; PSEi {snap['psei_index']}")
    if a.out:
        json.dump(snap, open(a.out, "w"), separators=(",", ":"))
        json.dump(bars_blob, open(a.out.replace(".json", "_bars.json"), "w"), separators=(",", ":"))
        json.dump(breadth, open(a.out.replace(".json", "_breadth.json"), "w"), separators=(",", ":"))
        print("wrote", a.out, "and the bars file beside it")
    if a.upload:
        if lr["kept"] < 40:
            return _fail(f"refusing to upload: only {lr['kept']} names kept (expect 100+)")
        if lr["n_failed"] > 0.3 * max(lr["tried"], 1):
            return _fail(f"refusing to upload: {lr['n_failed']} of {lr['tried']} reads failed")
        ph_data.write_blob("screener", snap)
        ph_data.write_blob("bars", bars_blob)
        if breadth:
            ph_data.write_blob("breadth", breadth)
        print("stored ph/screener.json, ph/bars.json" + (" and ph/breadth.json" if breadth else " (breadth not computed: too few names)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

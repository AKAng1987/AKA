"""Build the PSE EDGE snapshot (disclosures, results, buy-backs) for the CGI Philippines tab.

Run from ~/cgi-vercel with its venv (storage lives in api/ph_data.py):

    api/.venv/bin/python ~/market-dashboard/scripts/ph_edge_build.py --days 4 --out /tmp/edge.json      # build, local file only
    api/.venv/bin/python ~/market-dashboard/scripts/ph_edge_build.py --days 4 --upload                  # daily: merge into the stored snapshot
    api/.venv/bin/python ~/market-dashboard/scripts/ph_edge_build.py --days 120 --upload                # first run: backfill
    api/.venv/bin/python ~/market-dashboard/scripts/ph_edge_build.py --from /tmp/edge.json --upload      # store a file you already checked

The stored snapshot is the state: new rows are merged in by filing id (an amendment replaces the original), nothing is
fetched twice. Two seconds between requests: a 4-day window is a few minutes, a 120-day backfill about 40 minutes (add --history once for the filing dates).
Keeps titles, dates, a handful of figures and the EDGE link number; never a copy of a filing (EDGE terms).
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

KEEP_DISCLOSURE_DAYS = 120
KEEP_BUYBACK_DAYS = 400
KEEP_RESULTS_PER_SYMBOL = 6
KEEP_FILING_DATES_DAYS = 760     # two years: last year's date for the same report is what the expected-date estimate reads
MANILA = dt.timezone(dt.timedelta(hours=8))
a_notify = False


def _fail(msg: str) -> int:
    print(msg)
    if a_notify:
        subprocess.run(["osascript", "-e", f'display notification "{msg[:120]}" with title "CGI PH EDGE refresh"'.replace("'", "")], check=False)
    return 1


def merge(prev: dict | None, directory: list[dict], disclosures: list[dict], results: list[dict], buybacks: list[dict],
          today: dt.date, filing_dates: dict | None = None) -> dict:
    """Pure merge of new rows into the previous snapshot (tested)."""
    prev = prev or {}
    dr = {d["symbol"]: {k: d[k] for k in ("cmpy_id", "security_id", "name", "sector", "subsector")} for d in directory} or prev.get("directory", {})
    by = {r["edge_no"]: r for r in prev.get("disclosures", [])}
    by.update({r["edge_no"]: r for r in disclosures})
    cut = (today - dt.timedelta(days=KEEP_DISCLOSURE_DAYS)).isoformat()
    disc = sorted((r for r in by.values() if r["announced"][:10] >= cut), key=lambda r: r["announced"], reverse=True)

    res = {(r["symbol"], r["period"], r["kind"]): r for r in prev.get("results", [])}
    for r in results:
        k = (r["symbol"], r["period"], r["kind"])
        if k not in res or r["announced"] >= res[k]["announced"]:
            res[k] = r
    keep: dict[str, list] = {}
    for r in sorted(res.values(), key=lambda r: r["period"], reverse=True):
        keep.setdefault(r["symbol"], [])
        if len(keep[r["symbol"]]) < KEEP_RESULTS_PER_SYMBOL:
            keep[r["symbol"]].append(r)
    results_out = sorted((r for rows in keep.values() for r in rows), key=lambda r: r["announced"], reverse=True)

    bb = {(r["symbol"], r["report_date"]): r for r in prev.get("buybacks", [])}
    for r in buybacks:
        k = (r["symbol"], r["report_date"])
        if k not in bb or r["announced"] >= bb[k]["announced"]:
            bb[k] = r
    cut_b = (today - dt.timedelta(days=KEEP_BUYBACK_DAYS)).isoformat()
    bb_out = sorted((r for r in bb.values() if r["report_date"] >= cut_b), key=lambda r: r["report_date"], reverse=True)
    fd = {k: set(v) for k, v in (prev.get("filing_dates") or {}).items()}
    for sym, ds in (filing_dates or {}).items():
        fd.setdefault(sym, set()).update(ds)
    cut_f = (today - dt.timedelta(days=KEEP_FILING_DATES_DAYS)).isoformat()
    fd_out = {k: sorted(d for d in v if d >= cut_f) for k, v in fd.items()}
    return {"directory": dr, "disclosures": disc, "results": results_out, "buybacks": bb_out,
            "filing_dates": {k: v for k, v in fd_out.items() if v}}


def build(prev: dict | None, days: int, max_filings: int, history: bool = False, detail_days: int = 14, log=print) -> dict:
    today = dt.datetime.now(MANILA).date()
    d_from = today - dt.timedelta(days=days)
    directory = []
    if not prev or not prev.get("directory") or (dt.date.today() - dt.date.fromisoformat((prev.get("directory_at") or "2000-01-01"))).days > 7:
        log("directory ...")
        directory = E.fetch_directory()
    sym = {d["cmpy_id"]: d["symbol"] for d in directory} or {v["cmpy_id"]: k for k, v in (prev or {}).get("directory", {}).items()}
    log(f"disclosures {d_from} .. {today}")
    ann = []
    for step in range(0, days + 1, 14):          # 14-day windows keep every query to a few pages
        lo = d_from + dt.timedelta(days=step)
        ann += E.fetch_listing("announcements", lo, min(lo + dt.timedelta(days=13), today))
    parsed_prev = {r["edge_no"] for k in ("results", "buybacks") for r in (prev or {}).get(k, [])}   # a filing that failed to parse is tried again next run
    disclosures = []
    for r in ann:
        if r["form"] in E.SKIP_FORMS or r["cmpy_id"] not in sym:
            continue
        disclosures.append({"edge_no": r["edge_no"], "symbol": sym[r["cmpy_id"]], "company": r["company"], "chip": E.chip(r["form"]),
                            "form": r["form"], "title": r["title"][:120], "amend": r["amend"], "announced": r["announced"]})
    # subject lines and dividend / insider figures for the disclosures that matter, read once and kept
    prev_by = {r["edge_no"]: r for r in (prev or {}).get("disclosures", [])}
    for d in disclosures:
        old = prev_by.get(d["edge_no"])
        if old:
            d.update({k: old[k] for k in ("subject", "dividend", "insider") if k in old})
    detail_cut = (today - dt.timedelta(days=detail_days)).isoformat()
    need = sorted((d for d in disclosures if d["chip"] in E.DETAIL_CHIPS and "subject" not in d and d["announced"][:10] >= detail_cut),
                  key=lambda d: d["announced"], reverse=True)
    n_detail = n_detail_failed = 0
    for d in need[:300]:
        try:
            d.update(E.parse_detail(E.fetch_filing(d["edge_no"]), d["chip"]))
            n_detail += 1
        except Exception:  # noqa: BLE001 -- the row stays, with its generic title, and is tried again next run
            n_detail_failed += 1
    log(f"details read for {n_detail} disclosures ({n_detail_failed} failed, {max(len(need) - 300, 0)} left for the next run)")
    fin = []
    span = 430 if history else days            # --history: also list the last 14 months of 17-Q/17-A dates (not parsed) for the expected-date estimate
    for step in range(0, span + 1, 30):
        lo = today - dt.timedelta(days=span) + dt.timedelta(days=step)
        fin += E.fetch_listing("financialReports", lo, min(lo + dt.timedelta(days=29), today))
    filing_dates: dict[str, set] = {}
    for r in fin:
        if r["cmpy_id"] in sym and not r["amend"]:
            filing_dates.setdefault(sym[r["cmpy_id"]], set()).add(r["announced"][:10])
    todo = [(r, "results") for r in fin if r["edge_no"] not in parsed_prev and r["cmpy_id"] in sym and r["announced"][:10] >= d_from.isoformat()]
    todo += [(r, "buyback") for r in ann if r["form"] == "9-1" and r["edge_no"] not in parsed_prev and r["cmpy_id"] in sym]
    log(f"{len(disclosures)} disclosures, {len(todo)} filings to read")
    results, buybacks, failed = [], [], []
    for i, (r, what) in enumerate(todo[:max_filings]):
        try:
            html = E.fetch_filing(r["edge_no"])
            if what == "results":
                p = E.parse_results(html)
                if p is None:
                    raise ValueError("no income statement found")
                results.append({"symbol": sym[r["cmpy_id"]], "edge_no": r["edge_no"], "announced": r["announced"], "amend": r["amend"], **p})
            else:
                p = E.parse_buyback(html)
                if p is None:
                    raise ValueError("no trades found")
                buybacks.append({"symbol": sym[r["cmpy_id"]], "edge_no": r["edge_no"], "announced": r["announced"], "amend": r["amend"], **p})
        except Exception as e:  # noqa: BLE001
            failed.append({"edge_no": r["edge_no"], "symbol": sym.get(r["cmpy_id"]), "what": what, "why": str(e)[:100]})
        if i % 20 == 19:
            log(f"  {i + 1}/{min(len(todo), max_filings)}")
    snap = merge(prev, directory, disclosures, results, buybacks, today, filing_dates)
    snap.update({"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "window_days": days,
                 "directory_at": dt.date.today().isoformat() if directory else (prev or {}).get("directory_at"),
                 "last_run": {"disclosures": len(disclosures), "details_read": n_detail, "details_failed": n_detail_failed, "filings_tried": min(len(todo), max_filings), "results_parsed": len(results),
                              "buybacks_parsed": len(buybacks), "failed": failed[:30], "n_failed": len(failed)}})
    return snap


def main() -> int:
    global a_notify
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--detail-days", type=int, default=14, help="read subject/dividend/insider details for disclosures this recent")
    ap.add_argument("--history", action="store_true", help="also list 14 months of 17-Q/17-A dates (first run, then yearly)")
    ap.add_argument("--max-filings", type=int, default=600)
    ap.add_argument("--out")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--from", dest="src")
    ap.add_argument("--prev", help="start from this local snapshot instead of the stored one")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    a_notify = a.notify
    import ph_data

    if a.src:
        snap = json.load(open(a.src))
    else:
        prev = json.load(open(a.prev)) if a.prev else ph_data.read_blob("edge") if a.upload else None
        try:
            snap = build(prev, a.days, a.max_filings, a.history, a.detail_days)
        except Exception as e:  # noqa: BLE001
            return _fail(f"PH EDGE build failed: {e}")
    lr = snap.get("last_run", {})
    print(f"{len(snap['disclosures'])} disclosures, {len(snap['results'])} results, {len(snap['buybacks'])} buy-backs kept; "
          f"this run read {lr.get('results_parsed')} results + {lr.get('buybacks_parsed')} buy-backs, {lr.get('n_failed')} failed")
    for f in lr.get("failed", [])[:8]:
        print("  failed", f)
    if a.out:
        json.dump(snap, open(a.out, "w"), separators=(",", ":"))
        print("wrote", a.out)
    if a.upload:
        tried = lr.get("filings_tried", 0)
        if not a.src and not snap["disclosures"]:
            return _fail("refusing to upload: no disclosures at all")
        if tried >= 5 and lr.get("n_failed", 0) > 0.3 * tried:
            return _fail(f"refusing to upload: {lr['n_failed']} of {tried} filings failed to parse")
        ph_data.write_blob("edge", snap)
        print("stored ph/edge.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Check what the PH daily run actually stored and say so: one log line, ~/.cgi/ph_last_run.json, and a macOS notification only when something is wrong.

Run by ph_daily.sh after the builders (zero tokens; replaces a model-run check). It reads the stored snapshots (S3 ph/*.json) and answers:
  - did the snapshots store just now (edge, index, prices)?
  - after 15:10 Manila on a weekday: is the newest bar today's, and how many names have it (a PSE holiday and an EDGE lag look the same)?
  - did a large share of the filings fail to parse?
Usage (from ~/cgi-vercel with its venv):  api/.venv/bin/python ~/market-dashboard/scripts/ph_run_check.py --mode daily|edge [--notify]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
MANILA = dt.timezone(dt.timedelta(hours=8))
CLOSE_READY = dt.time(15, 10)        # the PSE closes at 15:00; the daily job runs at 15:10
FRESH_MINUTES = 90                   # a snapshot written by this run is younger than this
OUT = os.path.expanduser("~/.cgi/ph_last_run.json")


def expected_asof(now: dt.datetime) -> str:
    """The trading date the price snapshot should end on: today once past 15:10 on a weekday, else the last weekday before today."""
    d = now.date()
    if now.weekday() < 5 and now.time() >= CLOSE_READY:
        return d.isoformat()
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def _age_min(iso: str | None, now: dt.datetime) -> float | None:
    if not iso:
        return None
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return (now - t).total_seconds() / 60


def assess(now: dt.datetime, mode: str, screener: dict | None, edge: dict | None, index: dict | None, bars: dict | None, breadth: dict | None) -> dict:
    """Pure: the verdict for one run. mode 'daily' checks prices too; 'edge' (the evening catch-up) only EDGE and the PSE circular watcher."""
    msgs, info = [], {}
    utc = now.astimezone(dt.timezone.utc)
    ea = _age_min((edge or {}).get("built_at"), utc)
    info["edge_age_min"] = None if ea is None else round(ea)
    if ea is None or ea > FRESH_MINUTES:
        msgs.append("EDGE snapshot was not stored by this run")
    lr = (edge or {}).get("last_run") or {}
    tried, failed = lr.get("filings_tried", 0), lr.get("n_failed", 0)
    info["filings_tried"], info["filings_failed"] = tried, failed
    if tried >= 5 and failed > 0.3 * tried:
        msgs.append(f"{failed} of {tried} EDGE filings failed to parse: EDGE may have changed its pages")
    ia = _age_min((index or {}).get("scanned_at"), utc)
    info["index_age_min"] = None if ia is None else round(ia)
    if ia is None or ia > FRESH_MINUTES:
        msgs.append("PSE circular watcher did not store")
    if mode == "daily":
        want = expected_asof(now)
        asof = (screener or {}).get("asof")
        info["prices_asof"], info["expected_asof"] = asof, want
        pa = _age_min((screener or {}).get("built_at"), utc)
        if pa is None or pa > FRESH_MINUTES:
            msgs.append("price snapshot was not stored by this run")
        if asof is None or asof < want:
            last = {}
            for sym, rows in ((bars or {}).get("bars") or {}).items():
                if rows:
                    last[sym] = rows[-1][0]
            n_today = sum(1 for d in last.values() if d >= want)
            info["names_with_expected_bar"], info["names_total"] = n_today, len(last)
            msgs.append(f"no price bar for {want} yet ({n_today} of {len(last)} names have it): a PSE holiday, or EDGE has not published the close")
        if breadth and asof and breadth.get("as_of") and breadth["as_of"] != asof:
            msgs.append(f"breadth ends {breadth['as_of']} but prices end {asof}")
    return {"at": now.isoformat(timespec="seconds"), "mode": mode, "ok": not msgs, "messages": msgs, **info}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "edge"], default="daily")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    import ph_data

    now = dt.datetime.now(MANILA)
    r = assess(now, a.mode, ph_data.read_blob("screener"), ph_data.read_blob("edge"), ph_data.read_blob("index"), ph_data.read_blob("bars"), ph_data.read_blob("breadth"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(r, open(OUT, "w"), indent=1)
    print(f"ph_run_check {r['mode']}: {'OK' if r['ok'] else 'PROBLEM'} " + (f"prices to {r.get('prices_asof')}, " if r["mode"] == "daily" else "") +
          f"EDGE stored {r['edge_age_min']} min ago" + ("" if r["ok"] else " | " + " ; ".join(r["messages"])))
    if not r["ok"] and a.notify:
        subprocess.run(["osascript", "-e", f'display notification "{r["messages"][0][:110]}" with title "CGI Philippines run"'.replace("'", "")], check=False)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""
earnings_calendar.py -- who reports next, for the Fundamentals universe. Zero tokens: a plain script, no model, no login.

  python earnings_calendar.py            # fetch, compare with the stored snapshot, upload to S3
  python earnings_calendar.py --dry-run  # fetch and print; upload nothing

Source: TradingView's public scanner (https://scanner.tradingview.com/america/scan), columns earnings_release_next_date / _time,
earnings_publication_type_next_fq and earnings_release_date / _time. Checked 2026-11-19: its last-report date matched the company's
own 8-K Item 2.02 filing date for 83 of 83 names (all within a day); type 12 matched company-announced dates (CRM 2 Dec, AVGO 9 Dec).
Type 12 is labelled "confirmed"; types 10 and 11 are labelled "est." (type 10 carries a default 12:00 UTC stamp; 11 is unclassified).
This is an unofficial endpoint: the snapshot says where it came from, and the API flags it when it goes stale.

The snapshot also keeps (a) a short log of date changes and (b) a self-scoring log: the first date we held for a report vs the date the
company actually reported, so the accuracy of estimated dates is measured from here on rather than assumed.

S3: Cache/fundamentals/earnings_calendar.json (same bucket as the other CGI caches). Universe: the symbols the public /api/fundamentals
returns, with a local copy (~/.cgi/earnings_calendar_universe.json) for when it is down.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

SCANNER = "https://scanner.tradingview.com/america/scan"
UNIVERSE_URL = "https://cgi-vercel.vercel.app/api/fundamentals"
UNIVERSE_FILE = os.path.expanduser("~/.cgi/earnings_calendar_universe.json")
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
KEY = "Cache/fundamentals/earnings_calendar.json"
REGION = "ap-southeast-1"
US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSE ARCA", "ARCA", "BATS", "CBOE"}
ET = ZoneInfo("America/New_York")
KEEP_CHANGES, KEEP_SCORED = 300, 300
COLUMNS = ["name", "exchange", "type", "market_cap_basic", "earnings_release_next_date", "earnings_release_next_time",
           "earnings_publication_type_next_fq", "earnings_release_date", "earnings_release_time", "fiscal_period_current"]


# ---- pure parts (tested in check_earnings_calendar.py) ---------------------------------------------------------------------

def kind_of(code) -> str:
    """12 = the company has announced the date; everything else is an estimate."""
    return "confirmed" if code == 12 else "est."


def time_of(code) -> str | None:
    return {-1: "before open", 1: "after close"}.get(code)


def et_date(ts) -> str | None:
    if not isinstance(ts, (int, float)):
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone(ET).date().isoformat()


def pick_listing(rows: list[list]) -> list | None:
    """Several listings can share a name (foreign lines, ETFs). Take the stock on a US exchange with the largest market cap."""
    best = None
    for r in rows:
        name, exch, typ, cap = r[0], r[1], r[2], r[3] or 0
        if exch not in US_EXCHANGES or typ not in ("stock", "dr"):
            continue
        if best is None or cap > (best[3] or 0):
            best = r
    return best


def row_from(d: list) -> dict:
    return {"next": et_date(d[4]), "next_time": time_of(d[5]), "next_kind": kind_of(d[6]), "type_code": d[6],
            "last": et_date(d[7]), "last_time": time_of(d[8]), "fiscal_current": d[9]}


def diff_snapshots(prev: dict | None, rows: dict, today: str, scored: list, changes: list) -> tuple[list, list]:
    """Log date changes, and score each estimate against what the company then did.
    `prev` rows carry "held" = {date, kind, first_seen} for the report we were waiting on."""
    prev_rows = (prev or {}).get("rows", {})
    for sym, new in rows.items():
        old = prev_rows.get(sym)
        held = (old or {}).get("held") or ({"date": old["next"], "kind": old["next_kind"], "first_seen": (prev or {}).get("asof", today)[:10]}
                                           if old and old.get("next") else None)
        # a new report happened: its date moved past what we held
        if old and new["last"] and old.get("last") and new["last"] != old["last"] and held:
            miss = (dt.date.fromisoformat(new["last"]) - dt.date.fromisoformat(held["date"])).days
            scored.append({"symbol": sym, "held_date": held["date"], "kind": held["kind"], "first_seen": held["first_seen"],
                           "actual": new["last"], "miss_days": miss})
            held = None
        if new["next"] and (not held or held["date"] != new["next"] or held["kind"] != new["next_kind"]):
            if held and held["date"] != new["next"]:
                changes.append({"symbol": sym, "on": today, "from": held["date"], "from_kind": held["kind"],
                                "to": new["next"], "to_kind": new["next_kind"]})
            first = held["first_seen"] if held and held["date"] == new["next"] else today
            held = {"date": new["next"], "kind": new["next_kind"], "first_seen": first}
        new["held"] = held
    return scored[-KEEP_SCORED:], changes[-KEEP_CHANGES:]


# ---- plumbing ---------------------------------------------------------------------------------------------------------------

def _post(body: dict) -> dict:
    req = urllib.request.Request(SCANNER, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def universe() -> list[str]:
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(UNIVERSE_URL, headers={"User-Agent": "cgi-earnings-calendar"}), timeout=120))
        syms = sorted({c["symbol"] for c in d["companies"]})
        if syms:
            os.makedirs(os.path.dirname(UNIVERSE_FILE), exist_ok=True)
            json.dump(syms, open(UNIVERSE_FILE, "w"))
            return syms
    except Exception as exc:  # noqa: BLE001
        print(f"universe fetch failed ({exc}); using the saved copy")
    return json.load(open(UNIVERSE_FILE))


def fetch(names: list[str]) -> tuple[dict, list[str]]:
    out: dict = {}
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        r = _post({"filter": [{"left": "name", "operation": "in_range", "right": chunk}], "markets": ["america"],
                   "columns": COLUMNS, "range": [0, 1000]})
        by: dict = {}
        for d in r.get("data", []):
            by.setdefault(d["d"][0], []).append(d["d"])
        for n, rows in by.items():
            p = pick_listing(rows)
            if p:
                out[n] = row_from(p)
    return out, sorted(set(names) - set(out))


def s3():
    import boto3
    return boto3.client("s3", region_name=REGION)


def main() -> int:
    dry = "--dry-run" in sys.argv
    names = universe()
    rows, unresolved = fetch(names)
    today = dt.date.today().isoformat()
    prev = None
    try:
        prev = json.loads(s3().get_object(Bucket=BUCKET, Key=KEY)["Body"].read())
    except Exception:  # noqa: BLE001 -- first run: no previous snapshot
        pass
    scored, changes = diff_snapshots(prev, rows, today, list((prev or {}).get("scored", [])), list((prev or {}).get("changes", [])))
    snap = {"asof": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "source": "TradingView public scanner (unofficial)",
            "n_universe": len(names), "n_resolved": len(rows), "unresolved": unresolved, "rows": rows, "changes": changes, "scored": scored,
            "note": "type 12 = confirmed by the company; 10 and 11 = estimate"}
    due = sorted((r["next"], s, r["next_kind"]) for s, r in rows.items() if r["next"] and today <= r["next"] <= (dt.date.today() + dt.timedelta(days=14)).isoformat())
    print(f"{len(rows)}/{len(names)} resolved; unresolved {unresolved[:12]}; due in 14 days: {len(due)}; changes logged {len(changes)}; scored {len(scored)}")
    for d_, s_, k_ in due[:12]:
        print("  ", d_, s_, k_)
    if dry:
        return 0
    if len(rows) < 0.8 * len(names):
        print("REFUSING to upload: fewer than 80% of the universe resolved (the scanner may have changed)")
        return 1
    s3().put_object(Bucket=BUCKET, Key=KEY, Body=json.dumps(snap, separators=(",", ":")).encode(), ContentType="application/json")
    print("uploaded", KEY)
    return 0


if __name__ == "__main__":
    sys.exit(main())

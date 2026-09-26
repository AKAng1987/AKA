"""
Bulk-load TradingView ECONOMICS series into cmon-stage-backend-price-history.

Why this exists rather than POST /api/series: that endpoint caps at 24 rows
per call by design (it is public and unauthenticated), so a 120-bar backfill
would be 5 calls per symbol and a full-history one closer to 20. This writes
directly, the same way the other scripts/backfill_*.py do. The API path stays
the right one for the ongoing monthly routine, which only ever appends a row
or two.

Input: a JSON file of {"SYMBOL": [[unix_seconds, value], ...], ...} where
SYMBOL is the CGI name (PH_POLICY_RATE), not the TradingView one.

Bars are validated against series_write.ALLOWED's declared [min, max] before
anything is written, so a unit error -- the class of mistake that put a BOJ
balance sheet out by 1000x and still looked plausible -- fails loudly here
instead of rendering as a number on the page.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal

import boto3

REGION = "ap-southeast-1"
TABLE = "cmon-stage-backend-price-history"

sys.path.insert(0, "/Users/christinatan/cgi-vercel/api")
import ast

def allowed() -> dict:
    """Read ALLOWED without importing series_write (it pulls in fastapi)."""
    tree = ast.parse(open("/Users/christinatan/cgi-vercel/api/series_write.py").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ALLOWED":
            return ast.literal_eval(node.value)
    raise SystemExit("ALLOWED not found")


def main(path: str, dry_run: bool = True) -> None:
    ALLOWED = allowed()
    data = json.load(open(path))
    tbl = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    today = dt.date.today().isoformat()

    for sym, bars in data.items():
        if sym not in ALLOWED:
            print(f"SKIP {sym}: not in series_write.ALLOWED -- add it there first")
            continue
        _src, _freq, lo, hi, tv = ALLOWED[sym]
        rows, bad = [], []
        for t, v in bars:
            d = dt.datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d")
            if d > today:
                bad.append((d, v, "future date"))
                continue
            if v is None or not (lo <= float(v) <= hi):
                bad.append((d, v, f"outside declared range [{lo}, {hi}]"))
                continue
            rows.append({"symbol": sym, "date": d, "close": Decimal(str(v)), "source": tv})
        if bad:
            print(f"  {sym}: {len(bad)} rejected -- {bad[:3]}")
        if not rows:
            print(f"SKIP {sym}: nothing valid to write")
            continue
        print(f"{'DRY ' if dry_run else ''}{sym}: {len(rows)} rows "
              f"{rows[0]['date']} -> {rows[-1]['date']}, last={rows[-1]['close']} ({tv})")
        if not dry_run:
            with tbl.batch_writer(overwrite_by_pkeys=["symbol", "date"]) as b:
                for r in rows:
                    b.put_item(Item=r)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: load_country_series.py <bars.json> [--write]")
    main(sys.argv[1], dry_run="--write" not in sys.argv)

"""fx_hourly_rebuild.py -- re-date or fill FX history from Yahoo HOURLY bars (the close at 17:00 New York), the dating TradingView,
Alpha Vantage and FRED use. See ~/cmon-backend-service/src/external_api/fx_daily.py for why Yahoo's own daily FX bars cannot be
stored under their labels (they carry the PREVIOUS New York day's close, so stored as-is every row is a day off).

Dry run by default; nothing is written without --apply. Run from ~/cgi-vercel with its venv:

  api/.venv/bin/python ~/market-dashboard/scripts/fx_hourly_rebuild.py redate   --pairs USDCAD,USDMXN,... --since 2026-07-01 [--apply]
      replace the pair's yahoo_finance rows with correctly dated ones (the old rows are saved to data_manual/ first); dates that
      hold a row from ANOTHER source (FRED, Alpha Vantage) keep that row and get no Yahoo row.
  api/.venv/bin/python ~/market-dashboard/scripts/fx_hourly_rebuild.py backfill --pairs USDEUR,... --since 2026-09-01 [--apply]
      add yahoo_finance rows only for weekdays that hold NO row at all (insert-if-absent); nothing existing is touched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

import boto3
import yfinance as yf
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.expanduser("~/cmon-backend-service/src/external_api"))
import fx_daily  # noqa: E402

REGION = "ap-southeast-1"
PRICE = "cmon-stage-backend-price-history"
META = "cmon-stage-backend-metrics-source"
ET = ZoneInfo("America/New_York")
DATA = os.path.expanduser("~/market-dashboard/data_manual")
ddb = boto3.client("dynamodb", region_name=REGION)


def source_symbol(pair: str) -> str:
    """Yahoo ticker for a CGI FX pair: USDCAD -> CAD=X (units of the other currency per USD)."""
    return pair[3:] + "=X"


def complete_days(daily, now_et: dt.datetime) -> list[tuple[str, dict]]:
    """The finished New York sessions: dated before today, or today once 17:00 ET has passed (the adapter's `_complete`)."""
    out = []
    for ts, row in daily.iterrows():
        d = ts.date()
        if d.weekday() >= 5:
            continue
        if d < now_et.date() or (d == now_et.date() and now_et.hour >= 17):
            out.append((d.isoformat(), row))
    return out


def build(pair: str, since: str) -> dict[str, dict]:
    hs = (dt.date.fromisoformat(since) - dt.timedelta(days=4)).isoformat()
    h = yf.Ticker(source_symbol(pair)).history(start=hs, end=(dt.date.today() + dt.timedelta(days=2)).isoformat(), interval="1h")
    daily = fx_daily.daily_from_hourly(h)
    out = {}
    for d, r in complete_days(daily, dt.datetime.now(ET)):
        if d >= since:
            out[d] = {"close": float(r["Close"]), "open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"])}
    return out


def stored_rows(pair: str, since: str) -> dict[str, dict]:
    rows, key = {}, None
    while True:
        kw = dict(TableName=PRICE, KeyConditionExpression="symbol = :s AND #d >= :f",
                  ExpressionAttributeNames={"#d": "date"}, ExpressionAttributeValues={":s": {"S": pair}, ":f": {"S": since}})
        if key:
            kw["ExclusiveStartKey"] = key
        r = ddb.query(**kw)
        for it in r["Items"]:
            rows[it["date"]["S"]] = it
        key = r.get("LastEvaluatedKey")
        if not key:
            return rows


def item(pair: str, d: str, v: dict) -> dict:
    n = lambda x: {"N": repr(float(x))}
    return {"symbol": {"S": pair}, "date": {"S": d}, "source": {"S": "yahoo_finance"}, "source_symbol": {"S": source_symbol(pair)},
            "close": n(v["close"]), "adj_close": n(v["close"]), "open": n(v["open"]), "high": n(v["high"]), "low": n(v["low"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["redate", "backfill"])
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    pairs = a.pairs.split(",")
    backup = {}
    plan = {}
    for pair in pairs:
        new = build(pair, a.since)
        have = stored_rows(pair, a.since)
        other = {d for d, it in have.items() if it.get("source", {}).get("S") != "yahoo_finance"}
        old_y = {d: it for d, it in have.items() if it.get("source", {}).get("S") == "yahoo_finance"}
        if a.mode == "redate":
            dels = sorted(old_y)
            puts = {d: v for d, v in new.items() if d not in other}
            backup[pair] = [old_y[d] for d in dels]
            # A day the hourly feed lacks (thin pairs) is not left as a hole when the OLD data holds it: Yahoo's label was one weekday
            # late, so the old row labelled with the NEXT weekday carries that day's close. Use it for dates that would otherwise be empty.
            def nxt(d):
                x = dt.date.fromisoformat(d) + dt.timedelta(days=1)
                while x.weekday() >= 5:
                    x += dt.timedelta(days=1)
                return x.isoformat()

            def prv(d):
                x = dt.date.fromisoformat(d) - dt.timedelta(days=1)
                while x.weekday() >= 5:
                    x -= dt.timedelta(days=1)
                return x.isoformat()
            for d in dels:
                h = prv(d)
                if h >= a.since and h not in other and h not in puts:
                    it = old_y[d]
                    v = {k: float(it[k]["N"]) for k in ("close", "open", "high", "low") if k in it}
                    for k in ("open", "high", "low"):
                        v.setdefault(k, v["close"])
                    puts[h] = v
                    print(f"   {pair}: no hourly bar for {h}; filled from the old row labelled {d} (its close is {h}'s)")
        else:
            dels = []
            puts = {d: v for d, v in new.items() if d not in have}
        plan[pair] = {"delete": dels, "put": puts}
        moved = [abs(new[d]["close"] / float(old_y[d]["close"]["N"]) - 1) * 100 for d in dels if d in new]
        print(f"{pair}: {a.mode}  existing yahoo rows {len(old_y)} | other-source rows kept {len(other)} | to delete {len(dels)} | to put {len(puts)}"
              + (f" | new vs old close at the same label: mean {sum(moved)/len(moved):.3f}%" if moved else ""))
        missing = [d for d in dels if d not in puts and d not in other]
        if missing:
            print(f"   WARNING {pair}: would leave holes at {missing}")
        missing = [d for d in new if d not in puts and d not in have]
        if missing:
            print(f"   (no action for {missing})")
    if not a.apply:
        print("\nDRY RUN: nothing written. Re-run with --apply.")
        return 0
    if backup:
        p = os.path.join(DATA, f"price_history_yahoo_fx_redate_backup_{dt.date.today().isoformat()}.json")
        json.dump(backup, open(p, "w"), indent=1)
        print("backup written:", p)
    for pair, pl in plan.items():
        for d in pl["delete"]:
            ddb.delete_item(TableName=PRICE, Key={"symbol": {"S": pair}, "date": {"S": d}})
        for d, v in pl["put"].items():
            try:
                ddb.put_item(TableName=PRICE, Item=item(pair, d, v), ConditionExpression="attribute_not_exists(#d)",
                             ExpressionAttributeNames={"#d": "date"})
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        print(f"{pair}: applied ({len(pl['delete'])} deleted, {len(pl['put'])} put)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

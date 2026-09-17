"""
load_manual_series.py -- Load a monthly series from a CSV into price-history
in the same 5-field shape fred-data-updater writes, under its own source
name so no daily Lambda tries to refresh it.

Usage:
  SYMBOL=CHALLENGER SOURCE=challenger_gray FREQ=1M \
  CSV=data_manual/challenger_job_cuts.csv MONTH_COL=month VALUE_COL=job_cuts \
  DESC="Challenger announced job cuts (monthly)" \
  DRY_RUN=True  .venv/bin/python scripts/load_manual_series.py
  ...DRY_RUN=False to write (also ensures the metrics-source row exists).

Dates: month "YYYY-MM" -> "YYYY-MM-01" (same convention as FRED monthly).
Idempotent: existing dates are overwritten only with OVERWRITE=1.
"""
from __future__ import annotations

import csv
import os
from decimal import Decimal

import boto3

REGION = "ap-southeast-1"
PRICE = "cmon-stage-backend-price-history"
MSRC = "cmon-stage-backend-metrics-source"

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
OVERWRITE = os.environ.get("OVERWRITE") == "1"
SYMBOL, SOURCE, FREQ = os.environ["SYMBOL"], os.environ["SOURCE"], os.environ.get("FREQ", "1M")
CSV_PATH, MONTH_COL, VALUE_COL = os.environ["CSV"], os.environ.get("MONTH_COL", "month"), os.environ.get("VALUE_COL", "value")
DESC = os.environ.get("DESC", SYMBOL)

ddb = boto3.client("dynamodb", region_name=REGION)


def main() -> None:
    rows = []
    with open(CSV_PATH) as fh:
        for r in csv.DictReader(fh):
            v = (r.get(VALUE_COL) or "").strip()
            if not v:
                continue
            m = r[MONTH_COL].strip()
            date = m if len(m) == 10 else f"{m}-01"
            rows.append((date, Decimal(v.replace(",", ""))))
    rows.sort()
    print(f"=== {SYMBOL} ({SOURCE}, {FREQ}) -- {'DRY RUN' if DRY_RUN else 'LIVE'} -- {len(rows)} rows {rows[0][0]} .. {rows[-1][0]}")

    existing = set()
    kwargs = dict(TableName=PRICE, KeyConditionExpression="#s = :s",
                  ExpressionAttributeNames={"#s": "symbol", "#d": "date"},
                  ExpressionAttributeValues={":s": {"S": SYMBOL}}, ProjectionExpression="#d")
    while True:
        page = ddb.query(**kwargs)
        existing.update(i["date"]["S"] for i in page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    todo = [r for r in rows if OVERWRITE or r[0] not in existing]
    print(f"existing {len(existing)}, to write {len(todo)}{' (overwrite on)' if OVERWRITE else ''}")
    if DRY_RUN:
        print("sample:", todo[:2], "...", todo[-1:])
        return

    ms = ddb.get_item(TableName=MSRC, Key={"source": {"S": SOURCE}, "symbol": {"S": SYMBOL}}).get("Item")
    if not ms:
        ddb.put_item(TableName=MSRC, Item={"source": {"S": SOURCE}, "symbol": {"S": SYMBOL}, "source_symbol": {"S": SYMBOL},
                                           "frequency": {"S": FREQ}, "description": {"S": DESC}})
        print(f"metrics-source row created ({SOURCE}, {SYMBOL})")
    w = 0
    for date, val in todo:
        ddb.put_item(TableName=PRICE, Item={"symbol": {"S": SYMBOL}, "date": {"S": date}, "source": {"S": SOURCE},
                                            "source_symbol": {"S": SYMBOL}, "close": {"N": format(val.normalize(), "f")}})
        w += 1
    print(f"written {w}")


if __name__ == "__main__":
    main()

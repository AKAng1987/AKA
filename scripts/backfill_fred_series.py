"""
backfill_fred_series.py -- Backfill one FRED series into price-history in
the same 5-field shape fred-data-updater writes (source, source_symbol,
symbol, date, close). Idempotent: skips dates already present.

Usage:
  SERIES=BAA10Y DRY_RUN=True  .venv/bin/python scripts/backfill_fred_series.py
  SERIES=BAA10Y DRY_RUN=False .venv/bin/python scripts/backfill_fred_series.py

The series must already be onboarded to metrics-source on source=fred so
the daily Lambda keeps it current after the backfill.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import macro_data  # noqa: E402

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
SERIES = os.environ["SERIES"]
REGION = "ap-southeast-1"
TABLE = "cmon-stage-backend-price-history"


def main() -> None:
    print(f"=== backfill_fred_series.py {SERIES} -- {'DRY RUN' if DRY_RUN else 'LIVE WRITE'} ===")
    df = macro_data._fred_get(SERIES)
    print(f"FRED returned {len(df)} observations: {df['date'].min().date()} -> {df['date'].max().date()}")

    ddb = boto3.client("dynamodb", region_name=REGION)
    existing = set()
    for page in ddb.get_paginator("query").paginate(
        TableName=TABLE,
        KeyConditionExpression="#s = :s",
        ExpressionAttributeNames={"#s": "symbol", "#d": "date"},
        ExpressionAttributeValues={":s": {"S": SERIES}},
        ProjectionExpression="#d",
    ):
        existing.update(i["date"]["S"] for i in page["Items"])
    print(f"Existing rows: {len(existing)}")

    items = []
    for _, r in df.iterrows():
        d = r["date"].strftime("%Y-%m-%d")
        if d in existing:
            continue
        items.append({
            "symbol": {"S": SERIES},
            "date": {"S": d},
            "source": {"S": "fred"},
            "source_symbol": {"S": SERIES},
            "close": {"N": format(Decimal(str(r["value"])).normalize(), "f")},
        })
    print(f"Gap rows to write: {len(items)}")
    if items:
        print(f"  range: {items[0]['date']['S']} -> {items[-1]['date']['S']}")
        print(f"  sample: {items[0]}")

    if DRY_RUN:
        print("DRY RUN -- no writes.")
        return

    written = errors = 0
    for i, it in enumerate(items):
        try:
            ddb.put_item(TableName=TABLE, Item=it)
            written += 1
        except Exception as e:
            errors += 1
            print(f"  [WRITE ERROR] {it['date']['S']}: {e}")
        if (i + 1) % 1000 == 0:
            print(f"  ...{i + 1}/{len(items)}")
    print(f"Written: {written}  Errors: {errors}")


if __name__ == "__main__":
    main()

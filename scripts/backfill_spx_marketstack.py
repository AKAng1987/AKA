"""
backfill_spx_marketstack.py — Dry-run / live backfill of the SPX price-history
gap left after the 5B metrics-source migration (SPX: yahoo_finance -> marketstack).

Usage:
  DRY_RUN=True (default) — fetch data, build payloads, validate, report. Do NOT write.
  DRY_RUN=False           — write gap rows to DynamoDB.

DynamoDB table: cmon-stage-backend-price-history
  PK: symbol (String)
  SK: date   (String, format YYYY-MM-DD)

Row format (confirmed against an existing live marketstack-sourced row,
QQQ/2005-06-06, read directly from the table before writing this script):
  symbol        (S) — "SPX" (internal symbol)
  date          (S) — "YYYY-MM-DD"
  open          (N)
  high          (N)
  low           (N)
  close         (N)
  volume        (N)
  adj_open      (N)
  adj_high      (N)
  adj_low       (N)
  adj_close     (N)
  adj_volume    (N or NULL) — MarketStack returns null for index symbols;
                              production's add_price_data uses boto3's
                              resource-level put_item, which serializes
                              Python None -> DynamoDB NULL (not 0, not omitted).
                              This script matches that exactly.
  split_factor  (N)
  dividend      (N)
  source        (S) — "marketstack"
  source_symbol (S) — "GSPC.INDX"
  exchange      (S) — "INDX"
"""
from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal

import boto3
import requests

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN: bool = os.environ.get("DRY_RUN", "True").lower() != "false"

REGION         = "ap-southeast-1"
TABLE_NAME     = "cmon-stage-backend-price-history"
PRICE_UPDATER_LAMBDA = "cmon-stage-backend-price-updater"

SYMBOL          = "SPX"
SOURCE_SYMBOL   = "GSPC.INDX"
SOURCE          = "marketstack"
DATE_FROM       = "2026-05-20"
DATE_TO         = "2026-09-02"

NUMERIC_FIELDS = [
    "open", "high", "low", "close", "volume",
    "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume",
    "split_factor", "dividend",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_marketstack_api_key() -> str:
    """Pull MARKET_STACK_API_KEY from the price-updater Lambda's environment."""
    result = subprocess.run(
        [
            "aws", "lambda", "get-function-configuration",
            "--function-name", PRICE_UPDATER_LAMBDA,
            "--region", REGION,
            "--query", "Environment.Variables.MARKET_STACK_API_KEY",
            "--output", "text",
        ],
        capture_output=True, text=True, check=True,
    )
    key = result.stdout.strip()
    if not key or key == "None":
        raise RuntimeError("MARKET_STACK_API_KEY not found on price-updater Lambda")
    return key


def fetch_eod_range(api_key: str, symbol: str, date_from: str, date_to: str) -> list[dict]:
    resp = requests.get(
        "http://api.marketstack.com/v1/eod",
        params={
            "access_key": api_key,
            "symbols": symbol,
            "date_from": date_from,
            "date_to": date_to,
            "limit": 100,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", [])


def load_existing_dates(ddb_client, symbol: str) -> set:
    paginator = ddb_client.get_paginator("query")
    dates = set()
    for page in paginator.paginate(
        TableName=TABLE_NAME,
        KeyConditionExpression="#sym = :s",
        ExpressionAttributeNames={"#sym": "symbol", "#d": "date"},
        ExpressionAttributeValues={":s": {"S": symbol}},
        ProjectionExpression="#d",
    ):
        for item in page["Items"]:
            dates.add(item["date"]["S"])
    return dates


def _n(v) -> dict:
    """Numeric value -> DynamoDB typed dict; None -> NULL (matches production).

    DynamoDB's N type rejects exponential notation, so format as fixed-point
    rather than using Decimal.normalize() (which can emit "4.28685067E+9"
    for round numbers).
    """
    if v is None:
        return {"NULL": True}
    d = Decimal(str(v)).normalize()
    return {"N": format(d, "f")}


def build_item(record: dict) -> dict:
    date_str = record["date"][:10]
    item = {
        "symbol": {"S": SYMBOL},
        "date": {"S": date_str},
        "source": {"S": SOURCE},
        "source_symbol": {"S": SOURCE_SYMBOL},
        "exchange": {"S": record.get("exchange", "INDX")},
    }
    for field in NUMERIC_FIELDS:
        item[field] = _n(record.get(field))
    return item


def validate_item(item: dict) -> list[str]:
    errors = []
    required_s = ["symbol", "date", "source", "source_symbol", "exchange"]
    for k in required_s:
        if k not in item or "S" not in item[k]:
            errors.append(f"Missing/invalid S key: {k}")

    for k in NUMERIC_FIELDS:
        if k not in item:
            errors.append(f"Missing field: {k}")
            continue
        v = item[k]
        if "N" in v:
            try:
                Decimal(v["N"])
            except Exception:
                errors.append(f"Key {k} has non-numeric value: {v['N']!r}")
            else:
                if "E" in v["N"].upper():
                    errors.append(f"Key {k} uses exponential notation, invalid for DynamoDB N: {v['N']!r}")
        elif "NULL" not in v:
            errors.append(f"Key {k} has unexpected type: {v!r}")

    date_val = item.get("date", {}).get("S", "")
    if len(date_val) != 10 or date_val[4] != "-" or date_val[7] != "-":
        errors.append(f"Date format error: {date_val!r}")

    return errors


def write_item(ddb_client, item: dict) -> None:
    ddb_client.put_item(TableName=TABLE_NAME, Item=item)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    mode_label = "DRY RUN" if DRY_RUN else "LIVE WRITE"
    print(f"=== backfill_spx_marketstack.py — {mode_label} ===")
    print(f"Table: {TABLE_NAME}  Region: {REGION}")
    print(f"Symbol: {SYMBOL}  Source symbol: {SOURCE_SYMBOL}  Range: {DATE_FROM} -> {DATE_TO}")
    print()

    print("Fetching MARKET_STACK_API_KEY from price-updater Lambda env...")
    api_key = get_marketstack_api_key()
    print("Key retrieved (not printed).")
    print()

    print(f"Calling MarketStack /eod for {SOURCE_SYMBOL}...")
    records = fetch_eod_range(api_key, SOURCE_SYMBOL, DATE_FROM, DATE_TO)
    del api_key
    print(f"Records returned: {len(records)}")
    if not records:
        print("No records returned — nothing to do.")
        return

    dates = sorted(r["date"][:10] for r in records)
    print(f"Date range returned: {dates[0]} -> {dates[-1]}")
    print()

    ddb = boto3.client("dynamodb", region_name=REGION)

    print("Querying existing SPX dates from DynamoDB...")
    existing = load_existing_dates(ddb, SYMBOL)
    print(f"Existing SPX dates in price-history: {len(existing)}")

    records_by_date = {r["date"][:10]: r for r in records}
    gap_dates = sorted(d for d in records_by_date if d not in existing)
    overlap_dates = sorted(d for d in records_by_date if d in existing)

    print(f"Gap rows (would be written): {len(gap_dates)}")
    if overlap_dates:
        print(f"WARNING: {len(overlap_dates)} returned dates already exist in price-history and will be SKIPPED: {overlap_dates}")
    print()

    items = [build_item(records_by_date[d]) for d in gap_dates]

    format_errors = 0
    for item in items:
        errs = validate_item(item)
        if errs:
            format_errors += 1
            print(f"  [VALIDATION ERROR] {item['date']['S']}: {errs}")

    if format_errors == 0:
        print(f"Validation: all {len(items)} gap rows passed format checks.")
    else:
        print(f"Validation: {format_errors} rows failed format checks.")
    print()

    if items:
        print(f"Would write {len(items)} rows, date range {gap_dates[0]} -> {gap_dates[-1]}")
        print("Sample of 3 rows (verbatim DynamoDB item format):")
        import json
        sample_indices = [0, len(items) // 2, len(items) - 1]
        for i in sorted(set(sample_indices)):
            print(f"--- items[{i}] (date={items[i]['date']['S']}) ---")
            print(json.dumps(items[i], indent=2))

    if DRY_RUN:
        print()
        print("DRY RUN — no writes performed.")
    else:
        print()
        print(f"Writing {len(items)} rows to DynamoDB...")
        written = 0
        errors = 0
        for item in items:
            try:
                write_item(ddb, item)
                written += 1
            except Exception as e:
                errors += 1
                print(f"  [WRITE ERROR] {item['date']['S']}: {e}")
        print(f"Written: {written}, Errors: {errors}")

    print()
    print("=== Done ===")


if __name__ == "__main__":
    main()

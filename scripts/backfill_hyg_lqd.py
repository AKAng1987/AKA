"""
backfill_hyg_lqd.py -- Backfill HYG + LQD price-history for Markov Phase 2
HMM training. HYG (iShares iBoxx HY Corp Bond) and LQD (iShares iBoxx IG
Corp Bond) were onboarded to metrics-source on 2026-09-13 and started
collecting live daily rows on 2026-09-14. This script fills the historical
gap from 2008-03-27 (grid_US model-history start) up to the first live row.

Usage:
  DRY_RUN=True (default) -- fetch, validate, report. No writes.
  DRY_RUN=False           -- write gap rows to DynamoDB.

Follows the SPX backfill script's row format exactly, adapted for a
long-range paginated fetch: MarketStack /eod returns at most `limit`
rows per call, so we iterate year-by-year to keep each request
manageable.

DynamoDB table: cmon-stage-backend-price-history
  PK: symbol (String)
  SK: date   (String, YYYY-MM-DD)
"""
from __future__ import annotations

import os
import subprocess
from decimal import Decimal

import boto3
import requests

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN: bool = os.environ.get("DRY_RUN", "True").lower() != "false"

REGION         = "ap-southeast-1"
TABLE_NAME     = "cmon-stage-backend-price-history"
PRICE_UPDATER_LAMBDA = "cmon-stage-backend-price-updater"

# HYG and LQD both use source_symbol == symbol (per the metrics-source
# rows added 2026-09-13). Backfill only up to 2026-09-10 -- the live
# Lambda already wrote 2026-09-11 on 09-14, so anything from 09-11
# onwards is out of scope for the backfill and would overlap.
SYMBOLS = [("HYG", "HYG"), ("LQD", "LQD")]
DATE_FROM = "2008-03-27"  # grid_US model-history start
DATE_TO   = "2026-09-10"

# MarketStack paid plan supports limit=1000 per request.
PAGE_LIMIT = 1000

NUMERIC_FIELDS = [
    "open", "high", "low", "close", "volume",
    "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume",
    "split_factor", "dividend",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_marketstack_api_key() -> str:
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
    """Fetch all EOD rows for one symbol/date range, paginating via offset
    until the API stops returning new pages."""
    all_records: list[dict] = []
    offset = 0
    while True:
        resp = requests.get(
            "http://api.marketstack.com/v1/eod",
            params={
                "access_key": api_key,
                "symbols": symbol,
                "date_from": date_from,
                "date_to": date_to,
                "limit": PAGE_LIMIT,
                "offset": offset,
            },
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", [])
        if not data:
            break
        all_records.extend(data)
        # MarketStack's `pagination.total` is unreliable (observed reporting
        # total=1000 on offset=0 when a subsequent offset=1000 request still
        # returns 1000 more rows). Use the request-level signal instead:
        # a short page means the range is exhausted.
        if len(data) < PAGE_LIMIT:
            break
        offset += len(data)
    return all_records


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
    if v is None:
        return {"NULL": True}
    d = Decimal(str(v)).normalize()
    return {"N": format(d, "f")}


def build_item(symbol: str, source_symbol: str, record: dict) -> dict:
    date_str = record["date"][:10]
    item = {
        "symbol": {"S": symbol},
        "date": {"S": date_str},
        "source": {"S": "marketstack"},
        "source_symbol": {"S": source_symbol},
        "exchange": {"S": record.get("exchange", "")},
    }
    for field in NUMERIC_FIELDS:
        item[field] = _n(record.get(field))
    return item


def validate_item(item: dict) -> list[str]:
    errors = []
    for k in ["symbol", "date", "source", "source_symbol", "exchange"]:
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
                    errors.append(f"Key {k} uses exponential notation: {v['N']!r}")
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
    print(f"=== backfill_hyg_lqd.py -- {mode_label} ===")
    print(f"Table: {TABLE_NAME}  Region: {REGION}")
    print(f"Range: {DATE_FROM} -> {DATE_TO}")
    print()

    print("Fetching MARKET_STACK_API_KEY from price-updater Lambda env...")
    api_key = get_marketstack_api_key()
    print("Key retrieved.")
    print()

    ddb = boto3.client("dynamodb", region_name=REGION)

    total_would_write = 0
    total_written = 0
    total_errors = 0

    for symbol, source_symbol in SYMBOLS:
        print(f"--- {symbol} ({source_symbol}) ---")
        print(f"Calling MarketStack /eod paginated...")
        records = fetch_eod_range(api_key, source_symbol, DATE_FROM, DATE_TO)
        print(f"Records returned: {len(records)}")
        if not records:
            print(f"  No records for {symbol}, skipping.")
            continue
        dates = sorted(r["date"][:10] for r in records)
        print(f"  Date range: {dates[0]} -> {dates[-1]}")

        print(f"  Querying existing {symbol} dates from DynamoDB...")
        existing = load_existing_dates(ddb, symbol)
        print(f"  Existing rows: {len(existing)}")

        records_by_date = {r["date"][:10]: r for r in records}
        gap_dates = sorted(d for d in records_by_date if d not in existing)
        overlap_dates = sorted(d for d in records_by_date if d in existing)
        print(f"  Gap rows to write: {len(gap_dates)}")
        if overlap_dates:
            print(f"  Overlap (skipped): {len(overlap_dates)} rows")

        items = [build_item(symbol, source_symbol, records_by_date[d]) for d in gap_dates]
        format_errors = 0
        for item in items:
            errs = validate_item(item)
            if errs:
                format_errors += 1
                print(f"    [VALIDATION ERROR] {item['date']['S']}: {errs}")
        if format_errors:
            print(f"  Validation FAILED: {format_errors} rows -- skipping {symbol}")
            continue
        print(f"  Validation passed: {len(items)} rows")

        total_would_write += len(items)

        if not DRY_RUN and items:
            print(f"  Writing {len(items)} rows...")
            for i, item in enumerate(items):
                try:
                    write_item(ddb, item)
                    total_written += 1
                except Exception as e:
                    total_errors += 1
                    print(f"    [WRITE ERROR] {item['date']['S']}: {e}")
                if (i + 1) % 500 == 0:
                    print(f"    ...{i + 1}/{len(items)} written")
            print(f"  Done: {total_written} written this symbol")

        print()

    print(f"=== Summary ===")
    print(f"Total gap rows across all symbols: {total_would_write}")
    if not DRY_RUN:
        print(f"Total written: {total_written}")
        print(f"Total errors:  {total_errors}")
    else:
        print("DRY RUN -- no writes performed. Set DRY_RUN=False to write.")


if __name__ == "__main__":
    main()

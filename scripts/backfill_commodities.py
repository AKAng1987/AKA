"""
backfill_commodities.py — Production backfill of commodity symbols into
cmon-stage-backend-price-history, plus metrics-source row inserts.

Usage:
  DRY_RUN=True (default) — validate + print plan; no writes.
  DRY_RUN=False          — write price-history rows (batched, with retry)
                           AND insert metrics-source rows.

DynamoDB tables:
  cmon-stage-backend-price-history   PK: symbol (S)  SK: date (S)
  cmon-stage-backend-metrics-source  PK: source (S)  SK: symbol (S)
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path

import boto3
import pandas as pd
from botocore.exceptions import ClientError

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN: bool = os.environ.get("DRY_RUN", "True").lower() != "false"

REGION        = "ap-southeast-1"
PRICE_TABLE   = "cmon-stage-backend-price-history"
METRICS_TABLE = "cmon-stage-backend-metrics-source"
DATA_DIR      = Path(__file__).parent.parent / "data_download"

BATCH_SIZE    = 25   # DynamoDB batch_write_item maximum
MAX_RETRIES   = 7    # exponential back-off ceiling

# internal symbol → (parquet file, yfinance source_symbol, description)
SYMBOLS: dict[str, tuple[str, str, str]] = {
    "USOIL":   ("USOIL.parquet",   "CL=F",  "WTI Crude Oil Futures"),
    "NATGAS":  ("NATGAS.parquet",  "NG=F",  "Natural Gas Futures"),
    "SILVER":  ("SILVER.parquet",  "SI=F",  "Silver Futures"),
    "COPPER":  ("COPPER.parquet",  "HG=F",  "Copper Futures"),
    "RICE":    ("RICE.parquet",    "ZR=F",  "Rough Rice Futures"),
    "COTTON":  ("COTTON.parquet",  "CT=F",  "Cotton Futures"),
    "LITHIUM": ("LITHIUM.parquet", "LIT",   "Global X Lithium & Battery Tech ETF"),
    "URANIUM": ("URANIUM.parquet", "URA",   "Global X Uranium ETF"),
    "NICKEL":  ("NICKEL.parquet",  "NIKL",  "Sprott Nickel Miners ETF"),
    "COAL":    ("COAL.parquet",    "COAL",  "Range Global Coal Index ETF"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_existing_dates(ddb, symbol: str) -> set[str]:
    paginator = ddb.get_paginator("query")
    dates: set[str] = set()
    for page in paginator.paginate(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#sym = :s",
        ExpressionAttributeNames={"#sym": "symbol", "#d": "date"},
        ExpressionAttributeValues={":s": {"S": symbol}},
        ProjectionExpression="#d",
    ):
        for item in page["Items"]:
            dates.add(item["date"]["S"])
    return dates


def _n(v) -> str:
    try:
        return str(Decimal(str(float(v))).normalize())
    except Exception:
        return "0"


def build_item(symbol: str, row: pd.Series, source_symbol: str) -> dict:
    date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
    return {
        "symbol":        {"S": symbol},
        "date":          {"S": date_str},
        "open":          {"N": _n(row["open"])},
        "high":          {"N": _n(row["high"])},
        "low":           {"N": _n(row["low"])},
        "close":         {"N": _n(row["close"])},
        "adj_close":     {"N": _n(row["close"])},
        "volume":        {"N": _n(row.get("volume", 0))},
        "source":        {"S": "yahoo_finance"},
        "source_symbol": {"S": source_symbol},
    }


def validate_item(item: dict) -> list[str]:
    errors: list[str] = []
    for k in ["symbol", "date", "source", "source_symbol"]:
        if k not in item or "S" not in item[k]:
            errors.append(f"Bad S key: {k}")
    for k in ["open", "high", "low", "close", "adj_close", "volume"]:
        if k not in item or "N" not in item[k]:
            errors.append(f"Bad N key: {k}")
        else:
            try:
                Decimal(item[k]["N"])
            except Exception:
                errors.append(f"Non-numeric {k}: {item[k]['N']!r}")
    date_val = item.get("date", {}).get("S", "")
    if len(date_val) != 10 or date_val[4] != "-" or date_val[7] != "-":
        errors.append(f"Date format: {date_val!r}")
    return errors


def batch_write(ddb, table: str, items: list[dict]) -> tuple[int, int]:
    """Write items in 25-item batches with exponential back-off on UnprocessedItems.
    Returns (written_count, error_count)."""
    written = 0
    errors  = 0

    for chunk_start in range(0, len(items), BATCH_SIZE):
        chunk = items[chunk_start: chunk_start + BATCH_SIZE]
        pending = [{"PutRequest": {"Item": it}} for it in chunk]

        for attempt in range(MAX_RETRIES):
            try:
                resp = ddb.batch_write_item(RequestItems={table: pending})
                unprocessed = resp.get("UnprocessedItems", {}).get(table, [])
                written += len(pending) - len(unprocessed)
                if not unprocessed:
                    break
                pending = unprocessed
                delay = (2 ** attempt) * 0.1   # 0.1 s, 0.2 s, 0.4 s … up to ~6.4 s
                time.sleep(delay)
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code in ("ProvisionedThroughputExceededException",
                            "RequestLimitExceeded", "ThrottlingException"):
                    delay = (2 ** attempt) * 0.25
                    print(f"    Throttle on attempt {attempt + 1} — sleeping {delay:.2f}s")
                    time.sleep(delay)
                else:
                    errors += len(pending)
                    print(f"    [WRITE ERROR] {code}: {exc}")
                    break
        else:
            # Exhausted retries
            errors += len(pending)
            print(f"    [EXHAUSTED RETRIES] {len(pending)} items not written")

    return written, errors


def metrics_source_row(symbol: str, source_symbol: str, description: str) -> dict:
    """put_item payload for cmon-stage-backend-metrics-source."""
    return {
        "source":        {"S": "yahoo_finance"},
        "symbol":        {"S": symbol},
        "source_symbol": {"S": source_symbol},
        "frequency":     {"S": "1D"},
        "description":   {"S": description},
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    mode_label = "DRY RUN" if DRY_RUN else "LIVE WRITE"
    print(f"=== backfill_commodities.py — {mode_label} ===")
    print(f"Price table  : {PRICE_TABLE}")
    print(f"Metrics table: {METRICS_TABLE}  Region: {REGION}")
    print()

    ddb = boto3.client("dynamodb", region_name=REGION)

    # ── Step 1: price-history ─────────────────────────────────────────────────
    print("── STEP 1: price-history writes ──────────────────────────────────────")
    total_written = total_errors = 0
    per_symbol: list[dict] = []

    for symbol, (parquet_file, source_sym, description) in SYMBOLS.items():
        parquet_path = DATA_DIR / parquet_file
        if not parquet_path.exists():
            print(f"[{symbol}] ERROR: parquet not found: {parquet_path}")
            per_symbol.append({"symbol": symbol, "written": 0, "errors": 1,
                                "range": "—", "status": "MISSING PARQUET"})
            continue

        df = pd.read_parquet(parquet_path)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        df["_date_str"] = df["date"].dt.strftime("%Y-%m-%d")

        existing = load_existing_dates(ddb, symbol)
        if existing:
            print(f"[{symbol}] WARNING: {len(existing)} pre-existing rows in price-history")

        gap_df = df[~df["_date_str"].isin(existing)].copy()
        if gap_df.empty:
            print(f"[{symbol}] Nothing to write — skipping.")
            per_symbol.append({"symbol": symbol, "written": 0, "errors": 0,
                                "range": "already complete", "status": "SKIPPED"})
            continue

        date_range = f"{gap_df['_date_str'].min()} → {gap_df['_date_str'].max()}"

        # Build and validate items
        items = []
        format_errors = 0
        for _, row in gap_df.iterrows():
            item = build_item(symbol, row, source_sym)
            errs = validate_item(item)
            if errs:
                format_errors += 1
                print(f"  [VALIDATION ERROR] {row['_date_str']}: {errs}")
            else:
                items.append(item)

        if DRY_RUN:
            status = "DRY RUN"
            w = e = 0
            print(f"[{symbol}] DRY RUN — {len(items)} rows, {date_range}")
        else:
            print(f"[{symbol}] Writing {len(items)} rows ({date_range})…", flush=True)
            w, e = batch_write(ddb, PRICE_TABLE, items)
            e += format_errors
            total_written += w
            total_errors  += e
            status = "OK" if e == 0 else f"{e} ERRORS"
            print(f"[{symbol}] Written: {w}, Errors: {e}")

        per_symbol.append({"symbol": symbol, "written": w if not DRY_RUN else 0,
                            "errors": e if not DRY_RUN else 0,
                            "range": date_range, "status": status})

    print()

    # ── Step 2: metrics-source inserts ────────────────────────────────────────
    print("── STEP 2: metrics-source inserts ────────────────────────────────────")
    ms_results: list[tuple[str, str]] = []

    for symbol, (_, source_sym, description) in SYMBOLS.items():
        row = metrics_source_row(symbol, source_sym, description)
        flat = {k: list(v.values())[0] for k, v in row.items()}
        if DRY_RUN:
            print(f"[{symbol}] DRY RUN — would insert: {flat}")
            ms_results.append((symbol, "DRY RUN"))
        else:
            try:
                ddb.put_item(TableName=METRICS_TABLE, Item=row)
                print(f"[{symbol}] Inserted: {flat}")
                ms_results.append((symbol, "OK"))
            except Exception as exc:
                print(f"[{symbol}] ERROR inserting metrics-source row: {exc}")
                ms_results.append((symbol, f"ERROR: {exc}"))

    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"SUMMARY — {mode_label}")
    print()
    print(f"{'Symbol':<10} {'Written':>8} {'Errors':>7}  {'Date range':<40} Status")
    print("-" * 70)
    for row in per_symbol:
        print(f"{row['symbol']:<10} {row['written']:>8} {row['errors']:>7}  "
              f"{row['range']:<40} {row['status']}")
    if not DRY_RUN:
        print("-" * 70)
        print(f"{'TOTAL':<10} {total_written:>8} {total_errors:>7}")
    print()
    print("Metrics-source inserts:")
    for sym, status in ms_results:
        print(f"  {sym:<10} {status}")
    print()
    if not DRY_RUN:
        print("ACTION REQUIRED: click 'Load / Refresh All Price Data' in the")
        print("Streamlit sidebar to pull the new rows into the parquet cache.")
    print("=== Done ===")


if __name__ == "__main__":
    main()

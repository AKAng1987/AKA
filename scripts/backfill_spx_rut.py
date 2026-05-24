"""
backfill_spx_rut.py — Dry-run / live backfill of SPX and RUT gaps in DynamoDB.

Usage:
  DRY_RUN=True (default) — print counts and validate; do NOT write.
  DRY_RUN=False           — write gap rows to DynamoDB.

DynamoDB table: cmon-stage-backend-price-history
  PK: symbol (String)
  SK: date   (String, format YYYY-MM-DD)

Row format (modelled from existing yahoo_finance rows):
  symbol        (S) — e.g. "SPX"
  date          (S) — "YYYY-MM-DD"
  open          (N) — Decimal string
  high          (N) — Decimal string
  low           (N) — Decimal string
  close         (N) — Decimal string
  adj_close     (N) — same as close for indices (no adjustment needed)
  volume        (N) — Decimal string (0 for index symbols)
  source        (S) — "yahoo_finance"
  source_symbol (S) — "^GSPC" for SPX, "^RUT" for RUT
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────

DRY_RUN: bool = os.environ.get("DRY_RUN", "True").lower() != "false"

REGION        = "ap-southeast-1"
TABLE_NAME    = "cmon-stage-backend-price-history"
DATA_DIR      = Path(__file__).parent.parent / "data_download"

# Map internal symbol → (parquet filename, Yahoo source_symbol)
SYMBOLS = {
    "SPX": ("SPX.parquet", "^GSPC"),
    "RUT": ("RUT.parquet", "^RUT"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_existing_dates(ddb_client, symbol: str) -> set:
    """Query DynamoDB for all dates already stored for this symbol."""
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


def build_item(symbol: str, row: pd.Series, source_symbol: str) -> dict:
    """Convert a DataFrame row to a DynamoDB put_item payload."""
    date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")

    def _n(v) -> str:
        """Convert a value to a Decimal-safe numeric string."""
        try:
            return str(Decimal(str(float(v))).normalize())
        except Exception:
            return "0"

    return {
        "symbol":        {"S": symbol},
        "date":          {"S": date_str},
        "open":          {"N": _n(row["open"])},
        "high":          {"N": _n(row["high"])},
        "low":           {"N": _n(row["low"])},
        "close":         {"N": _n(row["close"])},
        "adj_close":     {"N": _n(row["close"])},   # no adjustment for indices
        "volume":        {"N": _n(row.get("volume", 0))},
        "source":        {"S": "yahoo_finance"},
        "source_symbol": {"S": source_symbol},
    }


def validate_item(item: dict) -> list[str]:
    """Return a list of format-validation errors for the item."""
    errors = []
    required_s = ["symbol", "date", "source", "source_symbol"]
    required_n = ["open", "high", "low", "close", "adj_close", "volume"]

    for k in required_s:
        if k not in item:
            errors.append(f"Missing S key: {k}")
        elif "S" not in item[k]:
            errors.append(f"Key {k} is not type S")

    for k in required_n:
        if k not in item:
            errors.append(f"Missing N key: {k}")
        elif "N" not in item[k]:
            errors.append(f"Key {k} is not type N")
        else:
            try:
                Decimal(item[k]["N"])
            except Exception:
                errors.append(f"Key {k} has non-numeric value: {item[k]['N']!r}")

    # Date format check
    date_val = item.get("date", {}).get("S", "")
    if len(date_val) != 10 or date_val[4] != "-" or date_val[7] != "-":
        errors.append(f"Date format error: {date_val!r}")

    return errors


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    mode_label = "DRY RUN" if DRY_RUN else "LIVE WRITE"
    print(f"=== backfill_spx_rut.py — {mode_label} ===")
    print(f"Table: {TABLE_NAME}  Region: {REGION}")
    print()

    ddb = boto3.client("dynamodb", region_name=REGION)

    for symbol, (parquet_file, source_sym) in SYMBOLS.items():
        parquet_path = DATA_DIR / parquet_file
        if not parquet_path.exists():
            print(f"[{symbol}] ERROR: parquet file not found: {parquet_path}")
            continue

        print(f"[{symbol}] Loading {parquet_file}...")
        df = pd.read_parquet(parquet_path)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        df["_date_str"] = df["date"].dt.strftime("%Y-%m-%d")

        print(f"[{symbol}] Downloaded rows: {len(df)}, range: {df['_date_str'].min()} → {df['_date_str'].max()}")

        print(f"[{symbol}] Querying existing dates from DynamoDB...")
        existing = load_existing_dates(ddb, symbol)
        print(f"[{symbol}] Existing DynamoDB dates: {len(existing)}")

        gap_df = df[~df["_date_str"].isin(existing)].copy()
        print(f"[{symbol}] Gap rows (would be written): {len(gap_df)}")

        if gap_df.empty:
            print(f"[{symbol}] No gaps — skipping.")
            print()
            continue

        print(f"[{symbol}] Gap date range: {gap_df['_date_str'].min()} → {gap_df['_date_str'].max()}")

        # Validate all gap rows
        format_errors = 0
        for _, row in gap_df.iterrows():
            item = build_item(symbol, row, source_sym)
            errs = validate_item(item)
            if errs:
                format_errors += 1
                print(f"  [VALIDATION ERROR] {row['_date_str']}: {errs}")

        if format_errors == 0:
            print(f"[{symbol}] Validation: all {len(gap_df)} gap rows passed format checks.")
        else:
            print(f"[{symbol}] Validation: {format_errors} rows failed format checks.")

        if DRY_RUN:
            print(f"[{symbol}] DRY RUN — no writes performed.")
        else:
            print(f"[{symbol}] Writing {len(gap_df)} rows to DynamoDB...")
            written = 0
            errors = 0
            for _, row in gap_df.iterrows():
                item = build_item(symbol, row, source_sym)
                try:
                    ddb.put_item(TableName=TABLE_NAME, Item=item)
                    written += 1
                except Exception as e:
                    errors += 1
                    print(f"  [WRITE ERROR] {row['_date_str']}: {e}")
            print(f"[{symbol}] Written: {written}, Errors: {errors}")

        print()

    print("=== Done ===")


if __name__ == "__main__":
    main()

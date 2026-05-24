"""
backfill_btc_eth.py — Re-source BTC and ETH from yfinance (BTC-USD / ETH-USD),
replacing the existing coingecko rows in price-history and switching the
metrics-source mappings.

What this script does when DRY_RUN=False:
  1. Writes the FULL yfinance date range for BTC and ETH to
     cmon-stage-backend-price-history using PutItem, which overwrites
     any existing row at the same (symbol, date) key.
     source = "yahoo_finance", source_symbol = "BTC-USD" / "ETH-USD".

  2. Does NOT touch metrics-source — the four metrics-source ops are
     printed for manual review only:
       - DELETE (source=coingecko, symbol=BTC)
       - DELETE (source=coingecko, symbol=ETH)
       - INSERT (source=yahoo_finance, symbol=BTC, source_symbol=BTC-USD)
       - INSERT (source=yahoo_finance, symbol=ETH, source_symbol=ETH-USD)

DynamoDB tables:
  cmon-stage-backend-price-history   PK: symbol (S)  SK: date (S)
  cmon-stage-backend-metrics-source  PK: source (S)  SK: symbol (S)

Usage:
  DRY_RUN=True  (default) — validate, print plan, show before/after sample
  DRY_RUN=False           — write price-history rows (batched, with retry)
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

BATCH_SIZE  = 25
MAX_RETRIES = 7

# internal symbol → (parquet file, yfinance source_symbol, description)
SYMBOLS: dict[str, tuple[str, str, str]] = {
    "BTC": ("BTC.parquet", "BTC-USD", "Bitcoin"),
    "ETH": ("ETH.parquet", "ETH-USD", "Ethereum"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_existing_rows(ddb, symbol: str) -> dict[str, dict]:
    """Return {date_str: item} for all existing rows of this symbol."""
    paginator = ddb.get_paginator("query")
    rows: dict[str, dict] = {}
    for page in paginator.paginate(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#sym = :s",
        ExpressionAttributeNames={"#sym": "symbol"},
        ExpressionAttributeValues={":s": {"S": symbol}},
    ):
        for item in page["Items"]:
            d = item["date"]["S"]
            rows[d] = item
    return rows


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
    """25-item batches with exponential back-off on UnprocessedItems / throttle."""
    written = errors = 0
    for chunk_start in range(0, len(items), BATCH_SIZE):
        chunk   = items[chunk_start: chunk_start + BATCH_SIZE]
        pending = [{"PutRequest": {"Item": it}} for it in chunk]
        for attempt in range(MAX_RETRIES):
            try:
                resp        = ddb.batch_write_item(RequestItems={table: pending})
                unprocessed = resp.get("UnprocessedItems", {}).get(table, [])
                written    += len(pending) - len(unprocessed)
                if not unprocessed:
                    break
                pending = unprocessed
                time.sleep((2 ** attempt) * 0.1)
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code in ("ProvisionedThroughputExceededException",
                            "RequestLimitExceeded", "ThrottlingException"):
                    delay = (2 ** attempt) * 0.25
                    print(f"    Throttle attempt {attempt + 1} — sleeping {delay:.2f}s")
                    time.sleep(delay)
                else:
                    errors += len(pending)
                    print(f"    [WRITE ERROR] {code}: {exc}")
                    break
        else:
            errors += len(pending)
            print(f"    [EXHAUSTED RETRIES] {len(pending)} items not written")
    return written, errors


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    mode_label = "DRY RUN" if DRY_RUN else "LIVE WRITE"
    print(f"=== backfill_btc_eth.py — {mode_label} ===")
    print(f"Price table  : {PRICE_TABLE}")
    print(f"Metrics table: {METRICS_TABLE}  Region: {REGION}")
    print()

    ddb = boto3.client("dynamodb", region_name=REGION)

    # ── Step 1: price-history ─────────────────────────────────────────────────
    print("── STEP 1: price-history writes (PutItem — overwrites by PK) ─────────")
    total_written = total_errors = 0
    per_symbol: list[dict] = []

    for symbol, (parquet_file, source_sym, description) in SYMBOLS.items():
        parquet_path = DATA_DIR / parquet_file
        if not parquet_path.exists():
            print(f"[{symbol}] ERROR: parquet not found: {parquet_path}")
            per_symbol.append({"symbol": symbol, "written": 0, "errors": 1,
                                "new": 0, "overwrite": 0, "status": "MISSING PARQUET"})
            continue

        df = pd.read_parquet(parquet_path)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        df["_date_str"] = df["date"].dt.strftime("%Y-%m-%d")
        print(f"[{symbol}] Parquet: {len(df)} rows, "
              f"{df['_date_str'].min()} → {df['_date_str'].max()}")

        print(f"[{symbol}] Loading existing DynamoDB rows…")
        existing = load_existing_rows(ddb, symbol)
        new_dates       = df[~df["_date_str"].isin(existing)]["_date_str"].tolist()
        overwrite_dates = df[ df["_date_str"].isin(existing)]["_date_str"].tolist()
        print(f"[{symbol}] Existing rows: {len(existing)}  "
              f"| New dates: {len(new_dates)}  | Overwrite dates: {len(overwrite_dates)}")

        # Sample overwrite: show one coingecko row vs what yfinance would write
        if overwrite_dates:
            sample_date = overwrite_dates[len(overwrite_dates) // 2]  # pick middle date
            old_item    = existing[sample_date]
            sample_row  = df[df["_date_str"] == sample_date].iloc[0]
            new_item    = build_item(symbol, sample_row, source_sym)
            def _fld(item, key):
                v = item.get(key, {})
                return list(v.values())[0] if v else "—"
            print(f"\n  Sample overwrite for {sample_date}:")
            print(f"  {'field':<14} {'BEFORE (coingecko)':>22} {'AFTER (yahoo_finance)':>22}")
            for fld in ["source", "source_symbol", "open", "high", "low", "close", "volume"]:
                b = _fld(old_item,  fld)
                a = _fld(new_item,  fld)
                mark = "  ←" if b != a else ""
                print(f"  {fld:<14} {str(b):>22} {str(a):>22}{mark}")
            print()

        # Build and validate all items
        items = []
        format_errors = 0
        for _, row in df.iterrows():
            item = build_item(symbol, row, source_sym)
            errs = validate_item(item)
            if errs:
                format_errors += 1
                print(f"  [VALIDATION ERROR] {row['_date_str']}: {errs}")
            else:
                items.append(item)

        date_range = f"{df['_date_str'].min()} → {df['_date_str'].max()}"

        if format_errors == 0:
            print(f"[{symbol}] Validation: all {len(items)} rows passed.")
        else:
            print(f"[{symbol}] Validation: {format_errors} rows FAILED.")

        if DRY_RUN:
            print(f"[{symbol}] DRY RUN — {len(items)} rows total "
                  f"({len(new_dates)} new, {len(overwrite_dates)} overwrite), "
                  f"{date_range}")
            w = e = 0
            status = "DRY RUN"
        else:
            print(f"[{symbol}] Writing {len(items)} rows ({date_range})…", flush=True)
            w, e = batch_write(ddb, PRICE_TABLE, items)
            e += format_errors
            total_written += w
            total_errors  += e
            status = "OK" if e == 0 else f"{e} ERRORS"
            print(f"[{symbol}] Written: {w}, Errors: {e}")

        per_symbol.append({
            "symbol":    symbol,
            "written":   w,
            "errors":    e,
            "new":       len(new_dates),
            "overwrite": len(overwrite_dates),
            "range":     date_range,
            "status":    status,
        })
        print()

    # ── Step 2: metrics-source ops preview ────────────────────────────────────
    print("── STEP 2: metrics-source ops (NOT executed — manual step) ───────────")
    ops = [
        ("DELETE", "coingecko",    "BTC", "BTC",     None,     "Bitcoin"),
        ("DELETE", "coingecko",    "ETH", "ETH",      None,    "Ethereum"),
        ("INSERT", "yahoo_finance","BTC", "BTC-USD", "1D",     "Bitcoin"),
        ("INSERT", "yahoo_finance","ETH", "ETH-USD", "1D",     "Ethereum"),
    ]
    for op, src, sym, src_sym, freq, desc in ops:
        if op == "DELETE":
            print(f"  {op}  key={{source={src!r}, symbol={sym!r}}}")
        else:
            print(f"  {op}  "
                  f"{{source={src!r}, symbol={sym!r}, source_symbol={src_sym!r}, "
                  f"frequency={freq!r}, description={desc!r}}}")
    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"SUMMARY — {mode_label}")
    print()
    hdr = f"{'Symbol':<8} {'Total':>7} {'New':>7} {'Overwrite':>10} {'Errors':>7}  Status"
    print(hdr)
    print("-" * 70)
    for row in per_symbol:
        print(f"{row['symbol']:<8} {row['new'] + row['overwrite']:>7} "
              f"{row['new']:>7} {row['overwrite']:>10} "
              f"{row['errors']:>7}  {row['status']}")
    if not DRY_RUN:
        print("-" * 70)
        print(f"{'TOTAL':<8} {total_written:>7}                        {total_errors:>7}")
    print()
    print("Metrics-source ops pending (manual):")
    for op, src, sym, *_ in ops:
        print(f"  {op:<7} source={src!r}, symbol={sym!r}")
    print()
    if not DRY_RUN:
        print("ACTION REQUIRED: click 'Load / Refresh All Price Data' in the")
        print("Streamlit sidebar to rebuild price_BTC.parquet / price_ETH.parquet.")
    print("=== Done ===")


if __name__ == "__main__":
    main()

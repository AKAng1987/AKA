"""
backfill_fx_alpha_vantage.py — one-time backfill of the price-history
gap left by the FRED DEX* freeze (2026-08-28 -> swap date), for FX pairs
migrating fred -> alpha_vantage per PHASE_FX_SWAP.md.

DRY_RUN=True (default) — fetch, build payloads, validate, report. No writes.
DRY_RUN=False           — write gap rows to DynamoDB.

Row format (confirmed against existing alpha_vantage rows, USDIDR,
read directly from price-history before writing this script):
  symbol        (S) — e.g. "USDJPY"
  date          (S) — "YYYY-MM-DD"
  open/high/low/close (N) — Decimal, fixed-point formatting (DynamoDB's
                             N type rejects exponential notation --
                             same lesson as backfill_spx_marketstack.py)
  source        (S) — "alpha_vantage"
  source_symbol (S) — e.g. "JPY" (bare code, not "USDJPY")
"""
import os
import time
from decimal import Decimal

import boto3
import requests

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
REGION = "ap-southeast-1"
TABLE_NAME = "cmon-stage-backend-price-history"
LAMBDA_FN = "cmon-stage-backend-alpha-vantage-updater"


def get_av_api_key() -> str:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if key:
        return key
    lam = boto3.client("lambda", region_name=REGION)
    cfg = lam.get_function_configuration(FunctionName=LAMBDA_FN)
    return cfg["Environment"]["Variables"]["ALPHA_VANTAGE_API_KEY"]


AV_API_KEY = get_av_api_key()

# Batch: remaining 10 pairs (USDJPY canary already done separately).
PAIRS = [
    ("USDCNY", "CNY"), ("USDHKD", "HKD"), ("USDINR", "INR"),
    ("USDKRW", "KRW"), ("USDSGD", "SGD"),
    ("USDCHF", "CHF"), ("USDTHB", "THB"),
    ("USDAUD", "AUD"), ("USDEUR", "EUR"), ("USDGBP", "GBP"),
]
GAP_START = "2026-08-29"  # day after FRED's last real observation


def fetch_fx_daily(av_symbol: str) -> dict:
    resp = requests.get("https://www.alphavantage.co/query", params={
        "function": "FX_DAILY", "from_symbol": "USD", "to_symbol": av_symbol,
        "outputsize": "compact", "apikey": AV_API_KEY,
    }, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    # Soft-error detection per memory: AV returns HTTP 200 with an
    # {"Information": ...} body on rate-limit -- must check for this
    # explicitly, same pattern already patched into the 7 provider
    # clients (silent-failure trap, Plan 3C).
    if "Information" in body or "Note" in body:
        raise RuntimeError(f"AV soft-error for {av_symbol}: {body}")
    return body.get("Time Series FX (Daily)", {})


def main():
    ddb = boto3.client("dynamodb", region_name=REGION)
    for symbol, av_symbol in PAIRS:
        series = fetch_fx_daily(av_symbol)
        gap_rows = {d: v for d, v in series.items() if d >= GAP_START}
        print(f"{symbol}: {len(gap_rows)} gap rows to backfill "
              f"({min(gap_rows) if gap_rows else '-'} .. "
              f"{max(gap_rows) if gap_rows else '-'})")
        for date, ohlc in sorted(gap_rows.items()):
            item = {
                "symbol": {"S": symbol}, "date": {"S": date},
                "open":  {"N": format(Decimal(ohlc["1. open"]),  "f")},
                "high":  {"N": format(Decimal(ohlc["2. high"]),  "f")},
                "low":   {"N": format(Decimal(ohlc["3. low"]),   "f")},
                "close": {"N": format(Decimal(ohlc["4. close"]), "f")},
                "source": {"S": "alpha_vantage"},
                "source_symbol": {"S": av_symbol},
            }
            if DRY_RUN:
                print("  [DRY RUN] would write:", item)
            else:
                ddb.put_item(TableName=TABLE_NAME, Item=item)
                print("  wrote:", date, "close=", item["close"]["N"])
        time.sleep(1)  # matches the 1-req/sec pacing hardcoded in
                        # cmon-stage-backend-alpha-vantage-updater


if __name__ == "__main__":
    main()

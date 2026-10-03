"""
backfill_fx_pairs.py -- load six USD pairs from FRED into price-history.

WHY FRED AND NOT TRADINGVIEW OR ALPHA VANTAGE

The plan was alpha_vantage. FRED turned out to be strictly better:

  - FRED's DEX* daily spot rates are free, unauthenticated and very deep
    (CAD and MYR go back to 1971-01-04).
  - They are already maintained nightly by fred-data-updater, so these
    pairs never join the token-dependent refresh routine.
  - alpha_vantage already has 15 FX registrations against a ~25/day free
    tier; seven more would have sat on the ceiling, and the failure mode
    there is a silent partial update.

Registering our own symbol against a different FRED id is an established
pattern, not a guess: GB10Y is live in production as
{source: fred, symbol: GB10Y, source_symbol: IRLTLT01GBM156N}.

WHAT IS DELIBERATELY NOT HERE

USDVND. FRED has no Vietnam series -- DEXVZUS is VENEZUELA (853.52 to the
dong's ~25,400), which would have been a plausible-looking FX line for the
wrong country entirely. TradingView does carry FX_IDC:USDVND, but the dong
is a crawling peg: 26,190 -> 25,984 over thirty weeks, a 1.7% range. A
near-flat series renders as a real FX read and invites a conclusion it
cannot support, so Vietnam keeps pair=None with that reason recorded.

All six are quoted LOCAL PER USD, matching the USDXXX convention the rest
of the FX set uses, so a positive return is dollar strength.

  DRY_RUN=False python3 scripts/backfill_fx_pairs.py
"""
from __future__ import annotations

import csv
import io
import os
import urllib.request
from decimal import Decimal

import boto3

REGION = "ap-southeast-1"
PRICE = "cmon-stage-backend-price-history"
MSRC = "cmon-stage-backend-metrics-source"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"

# symbol -> (FRED id, low, high, description)
# Bounds are per pair and deliberately wide enough for decades of history
# but tight enough that a decimal slip fails here rather than on the page.
PAIRS = {
    "USDCAD": ("DEXCAUS", 0.5, 5.0, "Canadian dollars per USD (FRED DEXCAUS)"),
    "USDMXN": ("DEXMXUS", 0.5, 60.0, "Mexican pesos per USD (FRED DEXMXUS)"),
    "USDZAR": ("DEXSFUS", 0.5, 60.0, "South African rand per USD (FRED DEXSFUS)"),
    "USDBRL": ("DEXBZUS", 0.1, 30.0, "Brazilian real per USD (FRED DEXBZUS)"),
    "USDTWD": ("DEXTAUS", 5.0, 70.0, "Taiwan dollars per USD (FRED DEXTAUS)"),
    "USDMYR": ("DEXMAUS", 0.5, 20.0, "Malaysian ringgit per USD (FRED DEXMAUS)"),
}

ddb = boto3.client("dynamodb", region_name=REGION)


def fetch(fred_id: str) -> list[tuple[str, float]]:
    raw = urllib.request.urlopen(FRED + fred_id, timeout=60).read().decode()
    out = []
    for row in list(csv.reader(io.StringIO(raw)))[1:]:
        if len(row) < 2 or row[1] in ("", "."):
            continue                      # FRED marks holidays with "."
        out.append((row[0], float(row[1])))
    return out


def existing_dates(symbol: str) -> set:
    seen, kw = set(), dict(
        TableName=PRICE, KeyConditionExpression="#s = :s",
        ExpressionAttributeNames={"#s": "symbol", "#d": "date"},
        ExpressionAttributeValues={":s": {"S": symbol}}, ProjectionExpression="#d")
    while True:
        p = ddb.query(**kw)
        seen |= {i["date"]["S"] for i in p["Items"]}
        if "LastEvaluatedKey" not in p:
            return seen
        kw["ExclusiveStartKey"] = p["LastEvaluatedKey"]


def main() -> None:
    tbl = boto3.resource("dynamodb", region_name=REGION).Table(PRICE)
    for sym, (fid, lo, hi, desc) in PAIRS.items():
        rows = fetch(fid)
        bad = [(d, v) for d, v in rows if not (lo <= v <= hi)]
        if bad:
            print(f"!! {sym}: {len(bad)} values outside [{lo}, {hi}] -- "
                  f"e.g. {bad[:3]}. NOT written.")
            continue
        have = existing_dates(sym)
        todo = [(d, v) for d, v in rows if d not in have]
        print(f"{'DRY ' if DRY_RUN else ''}{sym:7s} <- {fid:8s} {len(rows):6d} rows "
              f"{rows[0][0]} -> {rows[-1][0]}  last={rows[-1][1]}  new={len(todo)}")
        if DRY_RUN or not todo:
            continue
        with tbl.batch_writer(overwrite_by_pkeys=["symbol", "date"]) as b:
            for d, v in todo:
                b.put_item(Item={"symbol": sym, "date": d, "source": "fred",
                                 "source_symbol": fid,
                                 "close": Decimal(str(v))})
        ddb.put_item(TableName=MSRC, Item={
            "source": {"S": "fred"}, "symbol": {"S": sym},
            "source_symbol": {"S": fid}, "frequency": {"S": "1D"},
            "description": {"S": desc}})
        print(f"         registered {sym} -> fred/{fid} so the nightly Lambda maintains it")


if __name__ == "__main__":
    main()

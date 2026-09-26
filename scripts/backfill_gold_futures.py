"""
Onboard XAUUSD (Gold Futures, GC=F) and retire the mis-resolved GOLD symbol.

THE BUG THIS FIXES
`GOLD` in HUD_GROUPS["COMMODITIES CONT."] was never gold. It was ingested by
the marketstack EQUITY path on the NYSE ticker "GOLD", which is Barrick Gold
Corporation -- a mining stock. Its rows carry exchange=XNYS, a dividend field
and a split_factor, none of which a futures series has, and it traded at
$16 while GLD implied ~$4,284/oz.

It was also never registered in the metrics-source table, so nothing updated
it and it froze on 2023-09-01 -- 1,121 days before this was noticed. Every
other member of its group is one day old.

Consequences while it was live: a gold MINER's 2013-2023 history was being
reported as gold's regime edge (+8.94% against GLD's +1.27%), because miners
are levered to the metal -- in the 2018-06-14 window GOLD fell 15.7% where
GLD fell 6.2%.

THE FIX
A new symbol XAUUSD sourced from GC=F via yahoo_finance, the same path
SILVER (SI=F) and COPPER (HG=F) already use. Named XAUUSD rather than GOLD
precisely so it cannot collide with an equity ticker again -- the collision
IS the bug, and reusing the name would leave the trap armed.

Registered in metrics-source so the nightly price-updater maintains it.
Without that row a symbol silently stops updating, which is the second half
of what went wrong here.

NON-DESTRUCTIVE: the old GOLD rows are left in place. They become orphaned
once HUD_GROUPS points at XAUUSD. Deleting 2,560 rows is a call for the user
to make while awake, not something to do unattended.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from decimal import Decimal

import boto3

REGION = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"
METRICS_TABLE = "cmon-stage-backend-metrics-source"

SYMBOL = "XAUUSD"
SOURCE = "yahoo_finance"
SOURCE_SYMBOL = "GC=F"
DESCRIPTION = "Gold Futures"

# Sanity bounds. Gold has traded roughly $250-$5,000 in the modern era; a row
# outside this is a units or instrument error, which is exactly the failure
# being repaired -- Barrick at $16 would have been caught by this.
MIN_PX, MAX_PX = 100.0, 20_000.0


def fetch() -> list[dict]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?range=20y&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    r = d["chart"]["result"][0]
    meta = r["meta"]
    if meta.get("instrumentType") != "FUTURE" or meta.get("currency") != "USD":
        raise SystemExit(f"refusing: {SOURCE_SYMBOL} is not a USD future ({meta.get('instrumentType')})")
    q = r["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(r["timestamp"]):
        c = q["close"][i]
        if c is None:
            continue
        date = dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        if not (MIN_PX <= float(c) <= MAX_PX):
            print(f"  REJECT {date} close={c} outside [{MIN_PX}, {MAX_PX}]")
            continue
        row = {"symbol": SYMBOL, "date": date, "close": Decimal(str(round(float(c), 4))),
               "source": SOURCE, "source_symbol": SOURCE_SYMBOL}
        for src, dst in (("open", "open"), ("high", "high"), ("low", "low")):
            v = q[src][i]
            if v is not None:
                row[dst] = Decimal(str(round(float(v), 4)))
        v = q.get("volume", [None] * len(r["timestamp"]))[i]
        if v is not None:
            row["volume"] = Decimal(str(int(v)))
        out.append(row)
    return out


def main(write: bool) -> None:
    rows = fetch()
    print(f"{SOURCE_SYMBOL} -> {SYMBOL}: {len(rows)} rows "
          f"{rows[0]['date']} -> {rows[-1]['date']}, last={rows[-1]['close']}")
    if not write:
        print("DRY RUN -- pass --write to apply")
        return

    ddb = boto3.resource("dynamodb", region_name=REGION)
    tbl = ddb.Table(PRICE_TABLE)
    with tbl.batch_writer(overwrite_by_pkeys=["symbol", "date"]) as b:
        for r in rows:
            b.put_item(Item=r)
    print(f"wrote {len(rows)} rows to {PRICE_TABLE}")

    # Register for nightly maintenance. Skipping this is how GOLD froze.
    ddb.Table(METRICS_TABLE).put_item(Item={
        "source": SOURCE, "symbol": SYMBOL, "source_symbol": SOURCE_SYMBOL,
        "description": DESCRIPTION, "frequency": "1D",
    })
    print(f"registered {SYMBOL} in {METRICS_TABLE} (source={SOURCE}) so the "
          f"nightly price-updater keeps it current")


if __name__ == "__main__":
    main("--write" in sys.argv)

"""
Incremental sync: pulls any price-history rows dated >= yesterday from DynamoDB
and upserts them into the local SQLite. Run nightly via cron.

Usage:
    python scripts/sync_price_history.py

Cron example (runs at 6am daily):
    0 6 * * * cd ~/market-dashboard && python scripts/sync_price_history.py >> data/sync.log 2>&1
"""

import sqlite3
import boto3
from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path

TABLE_NAME = "cmon-stage-backend-price-history"
DB_PATH = Path(__file__).parent.parent / "data" / "price_history.db"

def parse_item(item):
    def val(v):
        if "S" in v:
            return v["S"]
        if "N" in v:
            return float(Decimal(v["N"]))
        return None

    return (
        val(item.get("symbol", {})),
        val(item.get("date", {})),
        val(item.get("open", {})),
        val(item.get("high", {})),
        val(item.get("low", {})),
        val(item.get("close", {})),
        val(item.get("volume", {})),
        val(item.get("adj_open", {})),
        val(item.get("adj_high", {})),
        val(item.get("adj_low", {})),
        val(item.get("adj_close", {})),
        val(item.get("adj_volume", {})),
        val(item.get("dividend", {})),
        val(item.get("split_factor", {})),
        val(item.get("exchange", {})),
        val(item.get("source", {})),
        val(item.get("source_symbol", {})),
    )

def main():
    if not DB_PATH.exists():
        print("ERROR: SQLite DB not found. Run export_price_history.py first.")
        return

    conn = sqlite3.connect(DB_PATH)
    since = (date.today() - timedelta(days=2)).isoformat()
    print(f"Syncing rows with date >= {since}...")

    dynamodb = boto3.client("dynamodb", region_name="ap-southeast-1")
    paginator = dynamodb.get_paginator("scan")
    pages = paginator.paginate(
        TableName=TABLE_NAME,
        FilterExpression="#d >= :since",
        ExpressionAttributeNames={"#d": "date"},
        ExpressionAttributeValues={":since": {"S": since}},
    )

    total = 0
    batch = []

    for page in pages:
        for item in page["Items"]:
            batch.append(parse_item(item))
            if len(batch) >= 500:
                conn.executemany(
                    "INSERT OR REPLACE INTO price_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
                conn.commit()
                total += len(batch)
                batch = []

    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO price_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch,
        )
        conn.commit()
        total += len(batch)

    conn.close()
    print(f"Sync complete. {total:,} rows upserted.")

if __name__ == "__main__":
    main()

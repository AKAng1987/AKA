"""
Full one-time export of DynamoDB price-history → local SQLite.
Run once on the office machine. Takes ~5-10 minutes for 834k rows.

Usage:
    python scripts/export_price_history.py
"""

import sqlite3
import boto3
from decimal import Decimal
from pathlib import Path

TABLE_NAME = "cmon-stage-backend-price-history"
DB_PATH = Path(__file__).parent.parent / "data" / "price_history.db"

def create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            symbol      TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            adj_open    REAL,
            adj_high    REAL,
            adj_low     REAL,
            adj_close   REAL,
            adj_volume  REAL,
            dividend    REAL,
            split_factor REAL,
            exchange    TEXT,
            source      TEXT,
            source_symbol TEXT,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON price_history (date)")
    conn.commit()

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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)

    dynamodb = boto3.client("dynamodb", region_name="ap-southeast-1")

    paginator = dynamodb.get_paginator("scan")
    pages = paginator.paginate(TableName=TABLE_NAME)

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
                print(f"  {total:,} rows written...", end="\r")

    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO price_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch,
        )
        conn.commit()
        total += len(batch)

    conn.close()
    print(f"\nDone. {total:,} rows saved to {DB_PATH}")

if __name__ == "__main__":
    main()

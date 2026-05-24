"""
Lightweight REST API serving price_history.db over the local network.
The other Claude instance (or any client) can query this instead of hitting DynamoDB.

Usage:
    pip install fastapi uvicorn
    python scripts/serve_api.py

Endpoints:
    GET /symbols                          — list all tickers
    GET /prices?symbol=SPY                — all rows for a symbol
    GET /prices?symbol=SPY&from=2024-01-01&to=2024-12-31
    GET /prices?symbol=SPY&limit=30       — most recent N rows
    GET /health                           — status + row count
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, Query, HTTPException
import uvicorn

DB_PATH = Path(__file__).parent.parent / "data" / "price_history.db"
app = FastAPI(title="Price History API")

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

@app.get("/health")
def health():
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        latest = conn.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    return {"status": "ok", "rows": count, "latest_date": latest, "db": str(DB_PATH)}

@app.get("/symbols")
def list_symbols():
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT symbol FROM price_history ORDER BY symbol").fetchall()
    return [r["symbol"] for r in rows]

@app.get("/prices")
def get_prices(
    symbol: str = Query(..., description="Ticker symbol, e.g. SPY"),
    from_date: str = Query(None, alias="from", description="Start date YYYY-MM-DD"),
    to_date: str = Query(None, alias="to", description="End date YYYY-MM-DD"),
    limit: int = Query(None, description="Most recent N rows"),
):
    query = "SELECT * FROM price_history WHERE symbol = ?"
    params = [symbol.upper()]

    if from_date:
        query += " AND date >= ?"
        params.append(from_date)
    if to_date:
        query += " AND date <= ?"
        params.append(to_date)

    query += " ORDER BY date DESC"

    if limit:
        query += f" LIMIT {int(limit)}"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No data found for symbol '{symbol}'")

    return [dict(r) for r in rows]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

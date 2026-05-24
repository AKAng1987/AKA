from __future__ import annotations
from typing import List, Optional
from pathlib import Path
import datetime
import os
import boto3
import pandas as pd
from boto3.dynamodb.types import TypeDeserializer

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _configure_aws() -> None:
    """Inject AWS credentials from st.secrets into env vars for boto3.

    On Streamlit Cloud, secrets are not auto-injected into os.environ.
    This bridges the gap so boto3's default credential chain picks them up.
    Falls through silently on local dev (uses ~/.aws/credentials instead).
    """
    try:
        import streamlit as st
        key    = st.secrets.get("AWS_ACCESS_KEY_ID", "")
        secret = st.secrets.get("AWS_SECRET_ACCESS_KEY", "")
        region = st.secrets.get("AWS_DEFAULT_REGION", "")
        if key:
            os.environ.setdefault("AWS_ACCESS_KEY_ID", key)
        if secret:
            os.environ.setdefault("AWS_SECRET_ACCESS_KEY", secret)
        if region:
            os.environ.setdefault("AWS_DEFAULT_REGION", region)
    except Exception:
        pass


_configure_aws()

REGION = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"
MODEL_TABLE = "cmon-stage-backend-model-history"

_ds = TypeDeserializer()

BACKTEST_PROXIES: dict = {}


def _deser(item: dict) -> dict:
    return {k: _ds.deserialize(v) for k, v in item.items()}


def cache_path(kind: str, name: str) -> Path:
    safe = name.replace("/", "_").replace("^", "").replace("-", "_")
    return CACHE_DIR / f"{kind}_{safe}.parquet"


def is_stale(path: Path, max_age_hours: float = 24.0) -> bool:
    if not path.exists():
        return True
    age = datetime.datetime.now().timestamp() - path.stat().st_mtime
    return age > max_age_hours * 3600


def _fetch_price_raw(symbol: str) -> pd.DataFrame:
    ddb = boto3.client("dynamodb", region_name=REGION)
    paginator = ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#sym = :s",
        ExpressionAttributeNames={"#sym": "symbol"},
        ExpressionAttributeValues={":s": {"S": symbol}},
    ):
        for item in page["Items"]:
            rows.append(_deser(item))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "adj_close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # Sources that only provide close (e.g. coingecko) — fill OHLC from close
    for col in ["open", "high", "low"]:
        if col not in df.columns:
            df[col] = df["close"]
    return df[["date", "open", "high", "low", "close"]].sort_values("date").reset_index(drop=True)


def _fetch_model_raw(model_name: str) -> pd.DataFrame:
    ddb = boto3.client("dynamodb", region_name=REGION)
    paginator = ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=MODEL_TABLE,
        KeyConditionExpression="model_name = :m",
        ExpressionAttributeValues={":m": {"S": model_name}},
        ScanIndexForward=True,
    ):
        for item in page["Items"]:
            rows.append(_deser(item))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["metrics_date"] = pd.to_datetime(df["metrics_date"])
    df["quadrant"] = pd.to_numeric(df["quadrant"], errors="coerce").astype("Int64")
    return df.sort_values("metrics_date").reset_index(drop=True)


def load_price(symbol: str, force: bool = False) -> pd.DataFrame:
    path = cache_path("price", symbol)
    if not force and not is_stale(path):
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    df = _fetch_price_raw(symbol)
    if not df.empty:
        df.to_parquet(path, index=False)
    return df


def load_model(model_name: str, force: bool = False) -> pd.DataFrame:
    path = cache_path("model", model_name)
    if not force and not is_stale(path):
        df = pd.read_parquet(path)
        df["metrics_date"] = pd.to_datetime(df["metrics_date"])
        df["quadrant"] = pd.to_numeric(df["quadrant"], errors="coerce").astype("Int64")
        return df
    df = _fetch_model_raw(model_name)
    if not df.empty:
        df.to_parquet(path, index=False)
    return df


def model_latest_date(model_name: str) -> Optional[pd.Timestamp]:
    path = cache_path("model", model_name)
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["metrics_date"])
    if df.empty:
        return None
    return pd.to_datetime(df["metrics_date"]).max()


def load_all_prices(symbols: List[str]) -> dict:
    """Read all available parquet files. Skips missing/corrupt files silently."""
    prices = {}
    for sym in symbols:
        proxy = BACKTEST_PROXIES.get(sym, sym)
        p = cache_path("price", proxy)
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            prices[sym] = df
        except Exception:
            pass
    return prices


def warm_prices(symbols: List[str], force: bool = False) -> List[str]:
    """Fetch and cache price history for all symbols. Returns list that had data."""
    loaded = []
    for sym in symbols:
        df = load_price(sym, force=force)
        if not df.empty:
            loaded.append(sym)
    return loaded


def cache_warm_count(symbols: List[str]) -> int:
    return sum(1 for s in symbols if cache_path("price", s).exists())

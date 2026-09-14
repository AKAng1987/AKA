"""
cmon-stage-backend-regime-divergence-updater -- Markov Phase 2 experimental
divergence layer. Runs 01:05 UTC, ten minutes after regime-signal-updater
(Phase 1) has written the day's row.

Per axis (credit <- compass_US, inflation <- grid_US):
  p_up             P(axis up | today's market), logistic on markov_features
  discrete_up      axis state implied by Phase 1's regime_current_discrete
  divergence_score 1 - P(discrete state)   (MARKOV_PHASE2_PLAN.md s2)
  direction        "market_up" / "market_down" when p_up and discrete_up
                   disagree at 0.5, else null. No threshold gating (s7.7).

Written as one top-level map `experimental_divergence` onto Phase 1's row
via UpdateItem. Phase 1 fields are never touched. If Phase 1's row for
the date is missing, log and exit (s7.8) -- do not create a row.

Event:
  {"dry_run": true}                 compute + log, no write
  {"signal_date": "YYYY-MM-DD"}     override date (default: today UTC)

Env:
  MODEL_BUCKET, MODEL_KEY           S3 location of the JSON artifact
  SIGNALS_TABLE, PRICE_TABLE
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
import numpy as np
import pandas as pd

import markov_features as mf

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("REGION", "ap-southeast-1")
PRICE_TABLE = os.environ.get("PRICE_TABLE", "cmon-stage-backend-price-history")
SIGNALS_TABLE = os.environ.get("SIGNALS_TABLE", "cmon-stage-backend-regime-signals")
MODEL_BUCKET = os.environ["MODEL_BUCKET"]
MODEL_KEY = os.environ["MODEL_KEY"]
MAX_FEATURE_STALENESS_DAYS = 5

_ddb = boto3.client("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_model_cache: dict = {}

# axis_key -> (Phase 1 regime_current_discrete key, index into Q_TO_AXES tuple)
AXES = {"credit": ("compass", 1), "inflation": ("grid", 1)}


def _n(v: float) -> dict:
    return {"N": format(Decimal(str(round(float(v), 6))).normalize(), "f")}


def load_model() -> dict:
    if MODEL_KEY in _model_cache:
        return _model_cache[MODEL_KEY]
    obj = _s3.get_object(Bucket=MODEL_BUCKET, Key=MODEL_KEY)
    art = json.loads(obj["Body"].read().decode("utf-8"))
    if art["feature_version"] != mf.FEATURE_VERSION:
        raise RuntimeError(
            f"artifact feature_version={art['feature_version']} != code FEATURE_VERSION={mf.FEATURE_VERSION}"
        )
    if art["feature_names"] != mf.FEATURE_NAMES:
        raise RuntimeError("artifact feature_names differ from markov_features.FEATURE_NAMES")
    _model_cache[MODEL_KEY] = art
    logger.info("loaded model %s trained_at=%s", art["model_version"], art["trained_at"])
    return art


def load_recent_close(symbol: str, since: str) -> pd.Series:
    rows = []
    kwargs = dict(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#s = :s AND #d >= :since",
        ExpressionAttributeNames={"#s": "symbol", "#d": "date", "#c": "close"},
        ExpressionAttributeValues={":s": {"S": symbol}, ":since": {"S": since}},
        ProjectionExpression="#d, #c",
    )
    while True:
        page = _ddb.query(**kwargs)
        for it in page["Items"]:
            if "close" in it and "N" in it["close"]:
                rows.append((it["date"]["S"], float(it["close"]["N"])))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    s = pd.Series(dict(rows), name=symbol, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def find_phase1_row(signal_date: str) -> dict | None:
    kwargs = dict(
        TableName=SIGNALS_TABLE,
        FilterExpression="signal_date = :d",
        ExpressionAttributeValues={":d": {"S": signal_date}},
    )
    items = []
    while True:
        page = _ddb.scan(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if not items:
        return None
    if len(items) > 1:
        logger.warning("multiple Phase 1 rows for %s (%d); using latest timestamp_utc", signal_date, len(items))
        items.sort(key=lambda i: i.get("timestamp_utc", {}).get("S", ""))
    return items[-1]


def score(model: dict, x: np.ndarray) -> float:
    z = (x - np.array(model["scaler_mean"])) / np.array(model["scaler_scale"])
    logit = float(z @ np.array(model["coef"]) + model["intercept"])
    return 1.0 / (1.0 + np.exp(-logit))


def lambda_handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run", False))
    signal_date = event.get("signal_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("start signal_date=%s dry_run=%s", signal_date, dry_run)

    art = load_model()

    since = (datetime.strptime(signal_date, "%Y-%m-%d") - timedelta(days=mf.MIN_HISTORY_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    closes = {sym: load_recent_close(sym, since) for sym in mf.INPUT_SYMBOLS}
    empty = [s for s, c in closes.items() if c.empty]
    if empty:
        logger.error("no price-history since %s for %s -- abort", since, empty)
        return {"status": "skipped", "reason": "missing_inputs", "symbols": empty}

    feats = mf.build_features(lambda s: closes[s])
    feats = feats.loc[feats.index <= pd.Timestamp(signal_date)]
    if feats.empty:
        logger.error("feature matrix empty up to %s -- abort", signal_date)
        return {"status": "skipped", "reason": "no_features"}
    features_as_of = feats.index[-1]
    staleness = (pd.Timestamp(signal_date) - features_as_of).days
    if staleness > MAX_FEATURE_STALENESS_DAYS:
        logger.error("features as of %s are %dd stale vs %s -- abort", features_as_of.date(), staleness, signal_date)
        return {"status": "skipped", "reason": "stale_features", "features_as_of": str(features_as_of.date())}
    x = feats.iloc[-1].values.astype(float)

    p1 = find_phase1_row(signal_date)
    if p1 is None:
        logger.error("no Phase 1 row for %s -- skipping (s7.8: do not create)", signal_date)
        return {"status": "skipped", "reason": "no_phase1_row"}
    discrete = {k: int(v["N"]) for k, v in p1["regime_current_discrete"]["M"].items()}

    result = {}
    for axis_key, (p1_key, idx) in AXES.items():
        q = discrete[p1_key]
        discrete_up = mf.Q_TO_AXES[q][idx]
        p_up = score(art["models"][axis_key], x)
        p_discrete = p_up if discrete_up else 1.0 - p_up
        market_up = int(p_up >= 0.5)
        direction = None if market_up == discrete_up else ("market_up" if market_up else "market_down")
        result[axis_key] = {
            "p_up": round(p_up, 6),
            "discrete_up": discrete_up,
            "discrete_quadrant": q,
            "divergence_score": round(1.0 - p_discrete, 6),
            "direction": direction,
        }
        logger.info("%s: p_up=%.3f discrete_up=%d (Q%d) divergence=%.3f direction=%s",
                    axis_key, p_up, discrete_up, q, 1.0 - p_discrete, direction)

    payload = {
        "experimental": True,
        "model_version": art["model_version"],
        "feature_version": art["feature_version"],
        "features_as_of": str(features_as_of.date()),
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "axes": result,
    }
    if dry_run:
        logger.info("dry_run -- would write: %s", json.dumps(payload))
        return {"status": "dry_run", "signal_id": p1["signal_id"]["S"], "payload": payload}

    def to_ddb(v):
        if isinstance(v, bool):
            return {"BOOL": v}
        if v is None:
            return {"NULL": True}
        if isinstance(v, (int, float)):
            return _n(v)
        if isinstance(v, str):
            return {"S": v}
        if isinstance(v, dict):
            return {"M": {k: to_ddb(x) for k, x in v.items()}}
        raise TypeError(type(v))

    _ddb.update_item(
        TableName=SIGNALS_TABLE,
        Key={"signal_id": p1["signal_id"], "signal_date": p1["signal_date"]},
        UpdateExpression="SET experimental_divergence = :v",
        ExpressionAttributeValues={":v": to_ddb(payload)},
    )
    logger.info("wrote experimental_divergence onto signal_id=%s", p1["signal_id"]["S"])
    return {"status": "ok", "signal_id": p1["signal_id"]["S"], "payload": payload}

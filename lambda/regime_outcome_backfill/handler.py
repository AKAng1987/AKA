"""
cmon-stage-backend-regime-outcome-backfill

Markov Phase 1 (MARKOV_PHASE1_PLAN.md). Daily job: find signals in
cmon-stage-backend-regime-signals old enough to check (1w/1m/3m) that
don't yet have that outcome populated, and fill it in with the actual
realized regime, SPX return, and whether the realized regime was among
the predicted top-3 per axis.

Access pattern note (documented choice, not an oversight -- see
MARKOV_PHASE1_PLAN.md): regime-signals' key schema is (signal_id HASH,
signal_date RANGE), optimized for point lookups by signal_id. This
job's real access pattern is date-based ("signals from ~N days ago"),
which the base table can't serve via Query. At current volume (~1
write/day) a Scan+FilterExpression is trivially cheap and the right
call for Phase 1; revisit with a signal_date GSI only if write volume
ever grows far past one signal/day.

Horizons are evaluated at the EXACT target date (signal_date + 7/30/90
calendar days) regardless of which day within the tolerance window this
job happens to catch them on -- the tolerance window only decides
*whether* to act today, not what date the outcome is measured as of.
Idempotent: only signals missing the given outcome_* attribute are
touched, so reruns and missed days are both safe.

Event payload:
  {"dry_run": true}  -- scan, compute, print what WOULD be written, no
                        UpdateItem calls.
  {}                 -- normal run: scan, compute, write.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

import boto3

REGION = "ap-southeast-1"
MODEL_TABLE = "cmon-stage-backend-model-history"
PRICE_TABLE = "cmon-stage-backend-price-history"
SIGNALS_TABLE = "cmon-stage-backend-regime-signals"

AXES = {
    "compass": "compass_US",
    "grid": "grid_US",
}

# (outcome_field, exact_horizon_days, tolerance_window_days)
HORIZONS = [
    ("outcome_1w", 7, (7, 10)),
    ("outcome_1m", 30, (30, 35)),
    ("outcome_3m", 90, (90, 97)),
]

_ddb = boto3.resource("dynamodb", region_name=REGION)


def get_latest_row(model_name: str, as_of: str) -> Optional[dict]:
    table = _ddb.Table(MODEL_TABLE)
    cond = boto3.dynamodb.conditions.Key("model_name").eq(model_name) & boto3.dynamodb.conditions.Key(
        "metrics_date"
    ).lte(as_of)
    resp = table.query(KeyConditionExpression=cond, ScanIndexForward=False, Limit=1)
    items = resp.get("Items", [])
    return items[0] if items else None


def get_latest_spx_close(as_of: str) -> Optional[dict]:
    table = _ddb.Table(PRICE_TABLE)
    cond = boto3.dynamodb.conditions.Key("symbol").eq("SPX") & boto3.dynamodb.conditions.Key("date").lte(as_of)
    resp = table.query(KeyConditionExpression=cond, ScanIndexForward=False, Limit=1)
    items = resp.get("Items", [])
    return items[0] if items else None


def candidates_for_horizon(today: datetime.date, outcome_field: str, tolerance: tuple[int, int]) -> list[dict]:
    """Scan regime-signals for rows aged within [tolerance[0], tolerance[1]]
    days that don't yet have outcome_field."""
    lo_days, hi_days = tolerance
    date_hi = (today - datetime.timedelta(days=lo_days)).isoformat()
    date_lo = (today - datetime.timedelta(days=hi_days)).isoformat()

    table = _ddb.Table(SIGNALS_TABLE)
    items = []
    scan_kwargs = {
        "FilterExpression": (
            boto3.dynamodb.conditions.Attr("signal_date").between(date_lo, date_hi)
            & boto3.dynamodb.conditions.Attr(outcome_field).not_exists()
        )
    }
    while True:
        resp = table.scan(**scan_kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def compute_outcome(signal: dict, horizon_days: int) -> dict:
    signal_date = datetime.date.fromisoformat(signal["signal_date"])
    check_date = (signal_date + datetime.timedelta(days=horizon_days)).isoformat()

    actual_regime = {}
    top3_hit = {}
    predicted = signal.get("next_regime_top3", {})
    for axis, model_name in AXES.items():
        row = get_latest_row(model_name, as_of=check_date)
        actual_q = int(row["quadrant"]) if row else None
        actual_regime[axis] = actual_q
        predicted_quadrants = {int(p["quadrant"]) for p in predicted.get(axis, [])}
        top3_hit[axis] = (actual_q in predicted_quadrants) if actual_q is not None else None

    spx_row = get_latest_spx_close(as_of=check_date)
    spx_close = Decimal(str(spx_row["close"])) if spx_row else None
    spx_return_pct = None
    prior_close = signal.get("spx_close_at_signal")
    if spx_close is not None and prior_close:
        spx_return_pct = Decimal(str(round(float((spx_close - prior_close) / prior_close) * 100, 4)))

    outcome = {
        "computed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "check_date": check_date,
        "actual_regime": actual_regime,
        "top3_hit": top3_hit,
    }
    if spx_close is not None:
        outcome["spx_close"] = spx_close
    if spx_return_pct is not None:
        outcome["spx_return_pct"] = spx_return_pct
    return outcome


def lambda_handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run", False))
    today = datetime.datetime.now(datetime.timezone.utc).date()

    table = _ddb.Table(SIGNALS_TABLE)
    written = []
    for outcome_field, horizon_days, tolerance in HORIZONS:
        candidates = candidates_for_horizon(today, outcome_field, tolerance)
        for sig in candidates:
            outcome = compute_outcome(sig, horizon_days)
            record = {
                "signal_id": sig["signal_id"],
                "signal_date": sig["signal_date"],
                "outcome_field": outcome_field,
                "outcome": _jsonable(outcome),
            }
            if dry_run:
                print(f"[regime-outcome-backfill] DRY RUN would write {outcome_field} for "
                      f"signal_id={sig['signal_id']} signal_date={sig['signal_date']}: {outcome}")
            else:
                table.update_item(
                    Key={"signal_id": sig["signal_id"], "signal_date": sig["signal_date"]},
                    UpdateExpression=f"SET {outcome_field} = :o",
                    ConditionExpression=boto3.dynamodb.conditions.Attr(outcome_field).not_exists(),
                    ExpressionAttributeValues={":o": outcome},
                )
                print(f"[regime-outcome-backfill] wrote {outcome_field} for "
                      f"signal_id={sig['signal_id']} signal_date={sig['signal_date']}")
            written.append(record)

    return {"dry_run": dry_run, "count": len(written), "items": written}


def _jsonable(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj

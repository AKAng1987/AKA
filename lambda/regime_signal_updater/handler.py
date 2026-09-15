"""
cmon-stage-backend-regime-signal-updater

Markov Phase 1 (MARKOV_PHASE1_PLAN.md). Daily job: recompute two
independent continuous-time Markov chains (compass_US, grid_US) fresh
from cmon-stage-backend-model-history, look up today's current regime
per axis, and write one row to cmon-stage-backend-regime-signals with
each axis's top-3 next-regime probabilities.

Design decisions (see MARKOV_PHASE1_PLAN.md for full rationale):
  - Two independent 4-state chains, not a joint 16-state model.
  - Continuous-time Markov chain (CTMC): exit rate q(i->j) = count(i->j
    transitions) / (winsorized total dwell time spent in state i).
    Dwell time per spell is winsorized at the 95th percentile
    (computed per-axis, over all spells) before summing, per
    MARKOV_SPEC.md's dwell-time constraint.
  - A "spell" is a maximal run of consecutive same-quadrant rows,
    merged into one -- compass_US has same-quadrant repeats (a new row
    logged on an underlying component signal change without the
    quadrant itself moving); grid_US does not, but the same merge logic
    is a no-op for it, so one code path handles both correctly.
  - Self-transitions are structurally impossible once spells are
    merged, so "top-3 next regime" is exactly "however many of the 3
    other states have been observed, ranked" -- no self-persistence
    candidate to special-case.
  - HMM/divergence fields (regime_current_hmm, confidence_hmm,
    divergence_flag, divergence_description) are Phase 2 -- not written
    here at all (DynamoDB is schemaless, so "not written" is simply
    absent, not null).

Phase 1.5 (2026-09-06, MARKOV_BACKLOG.md items 1-2): renamed
confidence_discrete -> top1_next_probability (it was only ever the
top-1 probability, not a calibrated confidence score) and added
transition_entropy (Shannon entropy in bits over the full next-state
distribution -- the real "how confident is this" signal: low entropy =
concentrated/confident, high = spread-out/uncertain). Also renamed
macro_prints_snapshot -> macro_prints_at_last_transition for honest
labeling (it's the metadata off the row that set the CURRENT regime,
not a fresh read -- current macro state is already served by the
LIVE/MACRO Vercel tabs; a live "today" fetch was considered and
deferred to Phase 2's HMM/divergence engine, see MARKOV_BACKLOG.md).

Percentile: implemented as plain-Python linear interpolation between
closest ranks, matching pandas'/numpy's default `interpolation="linear"`
exactly (h = (n-1)*q; result = x[floor(h)] + frac*(x[ceil(h)]-x[floor(h)])
on sorted data) -- deliberately not using pandas/numpy so this Lambda
has zero non-stdlib dependencies (avoids bundling Linux-wheel binaries),
while still being exactly reproducible against a local pandas-based
manual verification script.

Event payload:
  {"dry_run": true}          -- compute everything, write nothing,
                                return the full result for manual
                                inspection/verification.
  {}                         -- normal run: compute AND write today's
                                signal row to regime-signals.
  {"signal_id_override": ".."} -- overwrite an existing row's exact
                                signal_id instead of minting a new one
                                (used for the one-off Phase 1.5 schema
                                migration of the day-1 2026-09-06 row).
"""
from __future__ import annotations

import datetime
import math
import uuid
from decimal import Decimal
from typing import Optional

import boto3

import markov_events

REGION = "ap-southeast-1"
MODEL_TABLE = "cmon-stage-backend-model-history"
PRICE_TABLE = "cmon-stage-backend-price-history"
SIGNALS_TABLE = "cmon-stage-backend-regime-signals"

AXES = {
    "compass": "compass_US",
    "grid": "grid_US",
}

STATES = [1, 2, 3, 4]

_ddb = boto3.resource("dynamodb", region_name=REGION)


# ── Data access ─────────────────────────────────────────────────────

def query_full_history(model_name: str) -> list[dict]:
    """All rows for model_name, ascending by metrics_date."""
    table = _ddb.Table(MODEL_TABLE)
    items = []
    kwargs = {
        "KeyConditionExpression": boto3.dynamodb.conditions.Key("model_name").eq(model_name),
        "ScanIndexForward": True,
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    rows = [
        {"date": it["metrics_date"], "quadrant": int(it["quadrant"]), "metadata": it.get("metadata", {})}
        for it in items
    ]
    rows.sort(key=lambda r: r["date"])
    return rows


def get_latest_row(model_name: str, as_of: Optional[str] = None) -> Optional[dict]:
    """Latest row for model_name, optionally as-of a date (<=). None if
    no row exists (as_of before the series' first observation)."""
    table = _ddb.Table(MODEL_TABLE)
    cond = boto3.dynamodb.conditions.Key("model_name").eq(model_name)
    if as_of is not None:
        cond = cond & boto3.dynamodb.conditions.Key("metrics_date").lte(as_of)
    resp = table.query(KeyConditionExpression=cond, ScanIndexForward=False, Limit=1)
    items = resp.get("Items", [])
    return items[0] if items else None


def get_latest_spx_close(as_of: Optional[str] = None) -> Optional[dict]:
    table = _ddb.Table(PRICE_TABLE)
    cond = boto3.dynamodb.conditions.Key("symbol").eq("SPX")
    if as_of is not None:
        cond = cond & boto3.dynamodb.conditions.Key("date").lte(as_of)
    resp = table.query(KeyConditionExpression=cond, ScanIndexForward=False, Limit=1)
    items = resp.get("Items", [])
    return items[0] if items else None


# ── Spell / CTMC math ───────────────────────────────────────────────

def merge_spells(rows: list[dict]) -> list[dict]:
    """Collapse consecutive same-quadrant rows into one spell each.
    Returns spells ascending by start date; the LAST spell has
    end=None (still open as of the latest row -- excluded from
    transition counting since its duration is unknown/incomplete)."""
    if not rows:
        return []
    spells = []
    cur_q = rows[0]["quadrant"]
    cur_start = rows[0]["date"]
    for r in rows[1:]:
        if r["quadrant"] != cur_q:
            spells.append({"quadrant": cur_q, "start": cur_start, "end": r["date"]})
            cur_q = r["quadrant"]
            cur_start = r["date"]
    spells.append({"quadrant": cur_q, "start": cur_start, "end": None})
    return spells


def days_between(d1: str, d2: str) -> int:
    a = datetime.date.fromisoformat(d1)
    b = datetime.date.fromisoformat(d2)
    return (b - a).days


def percentile_linear(data: list[float], q: float) -> float:
    """Linear-interpolation percentile matching pandas/numpy's default
    exactly, for a local reproducibility check with zero non-stdlib deps."""
    if not data:
        return 0.0
    s = sorted(data)
    n = len(s)
    if n == 1:
        return float(s[0])
    h = (n - 1) * q
    lo = int(h)
    hi = min(lo + 1, n - 1)
    frac = h - lo
    return s[lo] + frac * (s[hi] - s[lo])


def build_ctmc(rows: list[dict]) -> dict:
    """rows: ascending [{date, quadrant}, ...] for one axis.

    Returns:
      matrix: {state: {to_state: probability, ...}, ...} -- only
              observed to_states are present; a 4-state chain has at
              most 3 entries per state (self-transitions are
              structurally impossible post-merge).
      n_historical_spells: {state: count of CLOSED spells in that state}
      total_dwell_days_in_state: {state: winsorized summed days}
    """
    spells = merge_spells(rows)
    closed = spells[:-1]  # exclude the final open spell

    durations_by_state: dict[int, list[int]] = {}
    trans_counts: dict[int, dict[int, int]] = {}
    for i, s in enumerate(closed):
        nxt = spells[i + 1]
        dur = days_between(s["start"], s["end"])
        durations_by_state.setdefault(s["quadrant"], []).append(dur)
        trans_counts.setdefault(s["quadrant"], {})
        trans_counts[s["quadrant"]][nxt["quadrant"]] = trans_counts[s["quadrant"]].get(nxt["quadrant"], 0) + 1

    all_durations = [d for durs in durations_by_state.values() for d in durs]
    cap = percentile_linear(all_durations, 0.95) if all_durations else None

    total_dwell: dict[int, float] = {}
    n_spells: dict[int, int] = {}
    for state, durs in durations_by_state.items():
        winsorized = [min(d, cap) if cap is not None else d for d in durs]
        total_dwell[state] = sum(winsorized)
        n_spells[state] = len(durs)

    matrix: dict[int, dict[int, float]] = {}
    for state in STATES:
        dwell = total_dwell.get(state, 0)
        counts = trans_counts.get(state, {})
        total_count = sum(counts.values())
        if dwell <= 0 or total_count == 0:
            matrix[state] = {}
            continue
        rates = {j: c / dwell for j, c in counts.items()}
        total_rate = sum(rates.values())
        matrix[state] = {j: r / total_rate for j, r in rates.items()} if total_rate > 0 else {}

    return {
        "matrix": matrix,
        "n_historical_spells": n_spells,
        "total_dwell_days_in_state": total_dwell,
    }


def top3(matrix_row: dict[int, float]) -> list[dict]:
    ranked = sorted(matrix_row.items(), key=lambda kv: kv[1], reverse=True)
    return [{"quadrant": q, "probability": Decimal(str(round(p, 6)))} for q, p in ranked[:3]]


def shannon_entropy_bits(matrix_row: dict[int, float]) -> float:
    """Shannon entropy, in bits (log base 2), over the full next-state
    distribution for one state (not just the top-3 slice -- for this
    4-state design the two are the same set, but computed over the full
    row for correctness if a future phase widens the state space).
    0 = fully concentrated on one outcome (maximally confident),
    higher = more spread across outcomes (maximally 1.585 bits for an
    even 3-way split, since self-transitions are structurally excluded
    and a 4-state chain has at most 3 next-state candidates)."""
    return -sum(p * math.log2(p) for p in matrix_row.values() if p > 0)


# ── Orchestration ───────────────────────────────────────────────────

def compute_signal(as_of: Optional[str] = None) -> dict:
    """Pure computation, no writes. as_of defaults to today (UTC)."""
    signal_date = as_of or datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    result = {"signal_date": signal_date}
    regime_current_discrete = {}
    top1_next_probability = {}
    transition_entropy = {}
    next_regime_top3 = {}
    n_historical_spells = {}
    total_dwell_days_in_state = {}
    macro_prints_at_last_transition = {}
    histories = {}

    for axis, model_name in AXES.items():
        history = query_full_history(model_name)
        histories[axis] = history
        ctmc = build_ctmc(history)

        latest = get_latest_row(model_name, as_of=signal_date)
        if latest is None:
            raise RuntimeError(f"No {model_name} row on or before {signal_date} -- cannot compute current regime")
        current_q = int(latest["quadrant"])
        regime_current_discrete[axis] = current_q

        current_row = ctmc["matrix"].get(current_q, {})
        row_top3 = top3(current_row)
        next_regime_top3[axis] = row_top3
        top1_next_probability[axis] = row_top3[0]["probability"] if row_top3 else Decimal("0")
        transition_entropy[axis] = Decimal(str(round(shannon_entropy_bits(current_row), 6)))

        n_historical_spells[axis] = {str(k): v for k, v in ctmc["n_historical_spells"].items()}
        total_dwell_days_in_state[axis] = {
            str(k): Decimal(str(round(v, 2))) for k, v in ctmc["total_dwell_days_in_state"].items()
        }
        macro_prints_at_last_transition[axis] = latest.get("metadata", {})

    spx_row = get_latest_spx_close(as_of=signal_date)
    spx_close_at_signal = Decimal(str(spx_row["close"])) if spx_row else None

    # Phase 1.5 pre-registration (MARKOV_BACKLOG.md item 9): one entry per
    # upcoming release with history + market P(flip). Best-effort and
    # additive -- if anything in it fails, the Phase 1 row is written
    # exactly as before with this field absent.
    upcoming_releases = None
    try:
        upcoming_releases = markov_events.build_upcoming(histories, regime_current_discrete, signal_date)
    except Exception as exc:  # noqa: BLE001
        print(f"[regime-signal-updater] upcoming_releases FAILED (Phase 1 fields unaffected): {exc!r}")

    result.update({
        "regime_current_discrete": regime_current_discrete,
        "top1_next_probability": top1_next_probability,
        "transition_entropy": transition_entropy,
        "next_regime_top3": next_regime_top3,
        "n_historical_spells": n_historical_spells,
        "total_dwell_days_in_state": total_dwell_days_in_state,
        "macro_prints_at_last_transition": macro_prints_at_last_transition,
        "spx_close_at_signal": spx_close_at_signal,
        "upcoming_releases": upcoming_releases,
    })
    return result


def write_signal(signal: dict, signal_id: Optional[str] = None) -> dict:
    """signal_id: pass an existing id to overwrite that exact row (e.g.
    a schema-migration correction of an already-written day) rather
    than the default of minting a fresh uuid4 for a new signal."""
    table = _ddb.Table(SIGNALS_TABLE)
    item = {
        "signal_id": signal_id or str(uuid.uuid4()),
        "signal_date": signal["signal_date"],
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regime_current_discrete": signal["regime_current_discrete"],
        "top1_next_probability": signal["top1_next_probability"],
        "transition_entropy": signal["transition_entropy"],
        "next_regime_top3": signal["next_regime_top3"],
        "n_historical_spells": signal["n_historical_spells"],
        "total_dwell_days_in_state": signal["total_dwell_days_in_state"],
        "macro_prints_at_last_transition": signal["macro_prints_at_last_transition"],
    }
    if signal.get("spx_close_at_signal") is not None:
        item["spx_close_at_signal"] = signal["spx_close_at_signal"]
    if signal.get("upcoming_releases"):
        item["upcoming_releases"] = signal["upcoming_releases"]
    table.put_item(Item=item)
    return item


def lambda_handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run", False))
    as_of = event.get("as_of_date")  # optional override, mainly for testing
    signal_id_override = event.get("signal_id_override")  # overwrite an existing row (schema migration)

    signal = compute_signal(as_of=as_of)

    if dry_run:
        print("[regime-signal-updater] DRY RUN -- not writing. Result:", signal)
        return {"dry_run": True, "signal": _jsonable(signal)}

    item = write_signal(signal, signal_id=signal_id_override)
    action = "overwrote" if signal_id_override else "wrote"
    print(f"[regime-signal-updater] {action} signal_id={item['signal_id']} signal_date={item['signal_date']}")
    return {"dry_run": False, "written": _jsonable(item)}


def _jsonable(obj):
    """Decimal -> float for logging/return-value readability only (the
    actual DynamoDB write uses Decimal directly via the resource API)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj

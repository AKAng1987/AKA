"""
fix_splits.py -- Repair unadjusted splits and feed glitches in price-history.

MarketStack serves raw closes: when an ETF splits, the series steps and the
step is indistinguishable from a real return. Inside the backtest that
fabricates a +/-60-80% move within whatever regime window contains the date,
which corrupts that ticker's avg_high / avg_low / edge for that regime.

Two repairs:
  GLITCH  one bad print that reverts next day -> delete that row
  SPLIT   a permanent level change at a ratio -> multiply every close
          BEFORE that date by the ratio, making returns continuous
          (levels become split-adjusted, which is what we want)

GENUINE moves are listed explicitly and never touched -- VIX spikes
(2018-02-05 volmageddon, 2024-08-05 carry unwind, etc.) and USOIL's
negative print on 2020-04-20 are real market history, not data errors.

Usage:
  DRY_RUN=True  .venv/bin/python scripts/fix_splits.py     # show the plan
  DRY_RUN=False .venv/bin/python scripts/fix_splits.py     # apply
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import boto3

sys.path.insert(0, str(Path.home() / "cgi-vercel" / "api"))
import axis_drivers as ad  # noqa: E402

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
REGION, TABLE = "ap-southeast-1", "cmon-stage-backend-price-history"
THRESH = 0.6

# Symbols whose large single-day moves are real. VIX routinely doubles;
# USOIL printed negative in April 2020. Never adjust these.
GENUINE = {"VIX", "USOIL", "NATGAS", "UNG"}   # UNG: real 2020-21 swings + a genuine split, handled by hand below
# Known real splits on symbols in GENUINE, applied explicitly.
FORCED_SPLITS = [("UNG", "2024-01-24", 4.19)]

ddb = boto3.client("dynamodb", region_name=REGION)


def classify(dates: list[str], closes: list[float]) -> list[tuple]:
    out = []
    for i in range(1, len(dates)):
        a, b = closes[i - 1], closes[i]
        if not a or not b:
            continue
        r = b / a
        if abs(r - 1) <= THRESH:
            continue
        j = min(i + 5, len(closes) - 1)
        after = closes[j]
        if not after:
            continue
        near_new = abs(after / b - 1) < 0.25
        near_old = abs(after / a - 1) < 0.25
        if near_old and not near_new:
            out.append(("GLITCH", dates[i], a, b, r))
        elif near_new and not near_old:
            out.append(("SPLIT", dates[i], a, b, r))
        else:
            out.append(("UNCLEAR", dates[i], a, b, r))
    return out


def main() -> None:
    import backtest_data as bd
    syms = sorted(set(bd.BACKTEST_UNIVERSE))
    plan_glitch: list[tuple[str, str]] = []
    plan_split: list[tuple[str, str, float]] = []

    # PASS 1 -- glitches only. A one-day bad print produces TWO large moves
    # (down then back up); the second looks exactly like a split. So the
    # glitch rows must be removed and the series re-read before any split
    # is judged, or the recovery leg gets back-adjusted by its own ratio.
    for s in syms:
        if s in GENUINE:
            continue
        try:
            d, c = ad._load_close(s)
        except Exception:
            continue
        for kind, date, a, b, r in classify(d, c):
            if kind == "GLITCH":
                plan_glitch.append((s, date))
                print(f"{s:7s} {date}  GLITCH  {a:.4g} -> {b:.4g}   delete row")
    if not DRY_RUN:
        for s, date in plan_glitch:
            ddb.delete_item(TableName=TABLE, Key={"symbol": {"S": s}, "date": {"S": date}})
        print(f"deleted {len(plan_glitch)} glitch rows\n")
    else:
        print(f"(dry run: would delete {len(plan_glitch)} glitch rows, then re-scan)\n")

    # PASS 2 -- splits, on the cleaned series.
    for s in syms:
        try:
            d, c = ad._load_close(s)
        except Exception as e:
            print(f"  {s}: load error {e}")
            continue
        if not d:
            continue
        if DRY_RUN:
            drop = {dt for sym, dt in plan_glitch if sym == s}
            if drop:
                keep = [(x, y) for x, y in zip(d, c) if x not in drop]
                d, c = [x for x, _ in keep], [y for _, y in keep]
        events = [e for e in classify(d, c) if e[0] != "GLITCH"]
        if s in GENUINE:
            if events:
                print(f"{s:7s} SKIPPED ({len(events)} large moves treated as genuine market history)")
            continue
        for kind, date, a, b, r in events:
            if kind == "SPLIT":
                plan_split.append((s, date, r))
                print(f"{s:7s} {date}  SPLIT   {a:.4g} -> {b:.4g}   ratio {r:.4g}, scale all rows before by {r:.4g}")
            else:
                print(f"{s:7s} {date}  UNCLEAR {a:.4g} -> {b:.4g}   LEFT ALONE, review by hand")

    for s, date, r in FORCED_SPLITS:
        plan_split.append((s, date, r))
        print(f"{s:7s} {date}  SPLIT   (forced) ratio {r}")

    print(f"\nplan: delete {len(plan_glitch)} glitch rows, back-adjust {len(plan_split)} splits")
    if DRY_RUN:
        print("DRY RUN -- nothing written.")
        return

    # Apply splits oldest-first per symbol so cumulative ratios compound correctly.
    by_sym: dict[str, list[tuple[str, float]]] = {}
    for s, date, r in plan_split:
        by_sym.setdefault(s, []).append((date, r))
    written = 0
    for s, evs in by_sym.items():
        evs.sort()
        d, c = ad._load_close(s)
        factor = {}
        for date, r in evs:
            for i, day in enumerate(d):
                if day < date:
                    factor[day] = factor.get(day, 1.0) * r
        for day, f in factor.items():
            i = d.index(day)
            new = c[i] * f
            ddb.update_item(TableName=TABLE, Key={"symbol": {"S": s}, "date": {"S": day}},
                            UpdateExpression="SET #c = :v",
                            ExpressionAttributeNames={"#c": "close"},
                            ExpressionAttributeValues={":v": {"N": format(Decimal(str(round(new, 6))).normalize(), "f")}})
            written += 1
        print(f"  {s}: adjusted {len(factor)} rows across {len(evs)} split(s)")
    print(f"back-adjusted {written} rows")


if __name__ == "__main__":
    main()

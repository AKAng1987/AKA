"""
Windowed-pandas replica verification: independently recompute
SPY's stats for grid_q=4, compass_q=2 (combo key "4_2") from raw
DynamoDB data using pandas, and diff against the Lambda's S3 output
for the same ticker/combo. Same discipline as the FX swap canary and
SPX derived-metrics repair verifications earlier in this project.
"""
import json
import boto3
import pandas as pd
from datetime import date, timedelta

REGION = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"
MODEL_TABLE = "cmon-stage-backend-model-history"

ddb = boto3.client("dynamodb", region_name=REGION)


def fetch_model(model_name):
    paginator = ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=MODEL_TABLE,
        KeyConditionExpression="model_name = :m",
        ExpressionAttributeValues={":m": {"S": model_name}},
        ScanIndexForward=True,
    ):
        for item in page["Items"]:
            rows.append({"metrics_date": item["metrics_date"]["S"], "quadrant": int(item["quadrant"]["N"])})
    return pd.DataFrame(rows)


def fetch_price(symbol):
    paginator = ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#sym = :s",
        ExpressionAttributeNames={"#sym": "symbol"},
        ExpressionAttributeValues={":s": {"S": symbol}},
    ):
        for item in page["Items"]:
            close = item.get("close", {}).get("N")
            if close is None:
                continue
            high = item.get("high", {}).get("N", close)
            low = item.get("low", {}).get("N", close)
            rows.append({"date": item["date"]["S"], "close": float(close), "high": float(high), "low": float(low)})
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df


grid = fetch_model("grid_US")
compass = fetch_model("compass_US")
prices = fetch_price("SPY")

today = date.today().isoformat()
grid_map = dict(zip(grid["metrics_date"], grid["quadrant"]))
compass_map = dict(zip(compass["metrics_date"], compass["quadrant"]))
all_dates = sorted(set(grid_map) | set(compass_map))

periods = []
gq = cq = None
for i, d in enumerate(all_dates):
    if d in grid_map:
        gq = grid_map[d]
    if d in compass_map:
        cq = compass_map[d]
    if gq is None or cq is None:
        continue
    next_date = all_dates[i + 1] if i + 1 < len(all_dates) else today
    end = (date.fromisoformat(next_date) - timedelta(days=1)).isoformat()
    if d <= end:
        periods.append({"start": d, "end": end, "grid_q": gq, "compass_q": cq})

GRID_Q, COMPASS_Q = 4, 2
matching = [p for p in periods if p["grid_q"] == GRID_Q and p["compass_q"] == COMPASS_Q]
print(f"matching periods for grid_q={GRID_Q}, compass_q={COMPASS_Q}: {len(matching)}")

occurrences = []
for period in matching:
    pp = prices[(prices["date"] >= period["start"]) & (prices["date"] <= period["end"])]
    if len(pp) < 1:
        continue
    entry_close = pp["close"].iloc[0]
    if not entry_close:
        continue
    exit_close = pp["close"].iloc[-1]
    max_high = pp["high"].max()
    min_low = pp["low"].min()
    start_d = pp["date"].iloc[0]
    end_d = pp["date"].iloc[-1]
    duration_days = (date.fromisoformat(end_d) - date.fromisoformat(start_d)).days
    occurrences.append({
        "start_date": start_d,
        "end_date": end_d,
        "duration_days": duration_days,
        "entry_close": round(float(entry_close), 4),
        "exit_close": round(float(exit_close), 4),
        "high_pct": round((max_high / entry_close - 1.0) * 100.0, 2),
        "low_pct": round((min_low / entry_close - 1.0) * 100.0, 2),
        "return_pct": round((exit_close / entry_close - 1.0) * 100.0, 2),
    })

count = len(occurrences)
avg_high = sum(o["high_pct"] for o in occurrences) / count
avg_low = sum(o["low_pct"] for o in occurrences) / count
hit_count = sum(1 for o in occurrences if o["return_pct"] > 0)
hit_rate = hit_count / count * 100.0
edge = (avg_high / abs(avg_low)) if avg_low < 0 else None
avg_return = sum(o["return_pct"] for o in occurrences) / count

replica_stats = {
    "count": count,
    "avg_high_pct": round(avg_high, 2),
    "avg_low_pct": round(avg_low, 2),
    "hit_rate": round(hit_rate, 1),
    "edge": round(edge, 2) if edge is not None else None,
    "avg_return": round(avg_return, 2),
}

print("replica occurrences:", json.dumps(occurrences, indent=2))
print("replica stats:", json.dumps(replica_stats, indent=2))

# --- Load Lambda's S3 output for the same ticker/combo ---
blob = json.load(open("/tmp/occurrences_blob.json"))
lambda_occ = blob["tickers"]["SPY"]["combos"].get("4_2", [])

print("\nlambda occurrences count:", len(lambda_occ))
print("lambda occurrences:", json.dumps(lambda_occ, indent=2))

# --- Diff ---
max_diff = 0.0
mismatches = []
if len(lambda_occ) != len(occurrences):
    mismatches.append(f"COUNT MISMATCH: replica={len(occurrences)} lambda={len(lambda_occ)}")
else:
    for i, (r, l) in enumerate(zip(occurrences, lambda_occ)):
        for k in ("entry_close", "exit_close", "high_pct", "low_pct", "return_pct"):
            diff = abs(r[k] - l[k])
            max_diff = max(max_diff, diff)
            if diff > 1e-6:
                mismatches.append(f"occurrence[{i}].{k}: replica={r[k]} lambda={l[k]} diff={diff}")
        for k in ("start_date", "end_date", "duration_days"):
            if r[k] != l[k]:
                mismatches.append(f"occurrence[{i}].{k}: replica={r[k]} lambda={l[k]}")

print(f"\nmax_diff (occurrence-level fields): {max_diff}")
print(f"mismatches: {len(mismatches)}")
for m in mismatches:
    print(" -", m)

print("\n=== GATE RESULT ===")
print("PASS" if not mismatches else "FAIL")

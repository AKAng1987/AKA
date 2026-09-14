#!/usr/bin/env bash
# verify_divergence_run.sh -- Morning check for the Markov pipeline.
# Read-only. Run from Terminal any time after ~09:20 local (01:05 UTC + jitter):
#   bash ~/market-dashboard/scripts/verify_divergence_run.sh            # today (UTC date)
#   bash ~/market-dashboard/scripts/verify_divergence_run.sh 2026-09-15 # specific date
set -u
R=ap-southeast-1
D="${1:-$(date -u +%Y-%m-%d)}"
T=cmon-stage-backend-regime-signals
ok()   { printf "  \033[32m✔\033[0m %s\n" "$*"; }
bad()  { printf "  \033[31m✘\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }

echo "== Markov pipeline check for signal_date=$D (now $(date -u +%H:%M) UTC) =="

ROW=$(aws dynamodb scan --region $R --table-name $T \
  --filter-expression "signal_date = :d" --expression-attribute-values "{\":d\":{\"S\":\"$D\"}}" \
  --projection-expression "signal_id, timestamp_utc, regime_current_discrete, experimental_divergence, outcome_1w" \
  --output json 2>/dev/null)

python3 - "$ROW" "$D" <<'EOF'
import json, sys
d = json.loads(sys.argv[1]) if sys.argv[1] else {"Items": []}
D = sys.argv[2]
items = d.get("Items", [])
G, R_, Y = "\033[32m✔\033[0m", "\033[31m✘\033[0m", "\033[33m!\033[0m"
if not items:
    print(f"  {R_} no Phase 1 row for {D} -- regime-signal-updater (00:55 UTC) did not write")
    sys.exit(0)
it = items[-1]
rc = {k: v["N"] for k, v in it["regime_current_discrete"]["M"].items()}
print(f"  {G} Phase 1 row present  ts={it['timestamp_utc']['S']}  compass=Q{rc['compass']} grid=G{rc['grid']}")
ed = it.get("experimental_divergence")
if not ed:
    print(f"  {R_} experimental_divergence MISSING -- divergence-updater (01:05 UTC) did not write")
else:
    m = ed["M"]
    print(f"  {G} experimental_divergence present  model={m['model_version']['S']}  computed_at={m['computed_at']['S']}  features_as_of={m['features_as_of']['S']}")
    for ax, v in m["axes"]["M"].items():
        a = v["M"]
        direction = a["direction"].get("S") if "S" in a["direction"] else "agree"
        print(f"      {ax:10s} p_up={float(a['p_up']['N']):.3f}  discrete_up={a['discrete_up']['N']} (Q{a['discrete_quadrant']['N']})  divergence={float(a['divergence_score']['N']):.3f}  {direction}")
EOF

echo "-- divergence-updater latest invocation --"
LG=/aws/lambda/cmon-stage-backend-regime-divergence-updater
STREAM=$(aws logs describe-log-streams --region $R --log-group-name $LG --order-by LastEventTime --descending --max-items 1 --query 'logStreams[0].logStreamName' --output text 2>/dev/null)
if [ -z "$STREAM" ] || [ "$STREAM" = "None" ]; then
  bad "no log streams"
else
  aws logs get-log-events --region $R --log-group-name $LG --log-stream-name "$STREAM" --limit 40 --query 'events[].message' --output text 2>/dev/null \
    | grep -E "start signal_date|wrote experimental|-- abort|-- skipping|ERROR|Traceback|REPORT" | sed 's/^/    /' | cut -c1-160
fi

echo "-- alarms --"
aws cloudwatch describe-alarms --region $R --alarm-name-prefix cmon-stage \
  --query 'MetricAlarms[].[AlarmName,StateValue]' --output text 2>/dev/null \
  | awk '{ s=$2; c=(s=="OK")?"\033[32m":(s=="ALARM")?"\033[31m":"\033[33m"; printf "    %s%-52s %s\033[0m\n", c, $1, s }'

echo "-- outcome backfill (row from 7 days before $D should now have outcome_1w) --"
D7=$(python3 -c "import datetime as d; print((d.date.fromisoformat('$D')-d.timedelta(days=7)).isoformat())")
HAS=$(aws dynamodb scan --region $R --table-name $T --filter-expression "signal_date = :d" \
  --expression-attribute-values "{\":d\":{\"S\":\"$D7\"}}" --projection-expression "outcome_1w" \
  --query 'Items[0].outcome_1w.M.top3_hit.M' --output json 2>/dev/null)
if [ -n "$HAS" ] && [ "$HAS" != "null" ]; then ok "$D7 row has outcome_1w: $(echo "$HAS" | tr -d ' \n')"; else warn "$D7 row has no outcome_1w yet"; fi
echo "== done =="

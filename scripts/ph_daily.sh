#!/bin/bash
# Daily Philippines refresh, run by launchd (com.arvin.ph-daily): PSE EDGE first (disclosures, results, buy-backs, company
# directory), then the price snapshot, which reads the directory. Zero tokens. Sundays rebuild every listed company; other days the
# names that trade. Failures raise a macOS notification and are listed in /api/freshness (ph).
set -u
PY=/Users/christinatan/cgi-vercel/api/.venv/bin/python
S=/Users/christinatan/market-dashboard/scripts
cd /Users/christinatan/cgi-vercel || exit 1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ph_daily"
$PY -u $S/ph_edge_build.py --days 4 --upload --notify || echo "edge build failed"
FULL=""
[ "$(date +%u)" = "7" ] && FULL="--full"
$PY -u $S/ph_prices_build.py $FULL --upload --notify || echo "prices build failed"

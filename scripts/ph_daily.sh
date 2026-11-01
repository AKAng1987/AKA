#!/bin/bash
# Philippines refresh, run by launchd (com.arvin.ph-daily), zero tokens. The same job every slot (the Mac's clock is Manila time):
#   weekdays 15:10   just after the PSE close at 15:00
#   weekdays 18:30   repeats it: late filings (17-Q, buy-backs, insider forms) and a retry if EDGE had not published the close at 15:10;
#                    also covers a 15:10 slot that ran late because the Mac was asleep
#   Saturday 09:00   the same with --full: every listed company is re-read, so a name that newly crosses the liquid line or a new listing enters
# Order: PSE EDGE (disclosures, results, buy-backs, directory), PSE circular watcher, prices + bars + breadth, then ph_run_check.py
# (one summary line in the log, a macOS notification only when something is wrong).
set -u
PY=/Users/christinatan/cgi-vercel/api/.venv/bin/python
S=/Users/christinatan/market-dashboard/scripts
cd /Users/christinatan/cgi-vercel || exit 1
DOW=$(date +%u)
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ph_daily (weekday $DOW)"
$PY -u $S/ph_edge_build.py --days 4 --upload --notify || echo "edge build failed"
$PY -u $S/ph_index_watch.py --upload --notify || echo "index watch failed"
FULL=""
[ "$DOW" = "6" ] && FULL="--full"
$PY -u $S/ph_prices_build.py $FULL --upload --notify || echo "prices build failed"
$PY -u $S/ph_run_check.py --mode daily --notify || echo "run check found a problem (see above)"

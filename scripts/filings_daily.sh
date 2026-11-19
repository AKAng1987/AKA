#!/bin/bash
# filings_daily.sh -- the daily filings job (launchd com.arvin.filings). Form 4 and 13D/G every run; 13F on Mondays (it only changes quarterly).
# The SEC user agent is read by filings_build.py from SEC_USER_AGENT or ~/.cgi/sec_user_agent.txt; this script never touches it.
PY=/Users/christinatan/cgi-vercel/api/.venv/bin/python
SCRIPT=/Users/christinatan/market-dashboard/scripts/filings_build.py
echo "== filings $(date '+%Y-%m-%d %H:%M')"
$PY -W ignore "$SCRIPT" --daily
if [ "$(date +%u)" = "1" ]; then
  $PY -W ignore "$SCRIPT" --13f
fi

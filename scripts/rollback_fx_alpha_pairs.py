"""rollback_fx_alpha_pairs.py -- put the 15 FX pairs back on Alpha Vantage (undo the 2026-10-30 move to Yahoo hourly).

What it does: for each pair, re-creates the alpha_vantage row in cmon-stage-backend-metrics-source from the backup
(~/market-dashboard/data_manual/metrics_source_alpha_fx15_backup_2026-10-30.json) and deletes the pair's yahoo_finance row. Price
history is NOT touched (the rows already stored stay; Alpha Vantage's own run keeps the newest 5 candles and heals gaps). The
Alpha Vantage schedule must be ENABLED (rule cmon-stage-backend-AlphaVantageUpdaterSchedule, cron 00:12 UTC, 25 calls a day on
the free tier, 15 used).

  api/.venv/bin/python scripts/rollback_fx_alpha_pairs.py            # dry run
  api/.venv/bin/python scripts/rollback_fx_alpha_pairs.py --apply
"""
import json
import os
import sys

import boto3

REGION, TABLE = "ap-southeast-1", "cmon-stage-backend-metrics-source"
BACKUP = os.path.expanduser("~/market-dashboard/data_manual/metrics_source_alpha_fx15_backup_2026-10-30.json")


def main() -> int:
    apply = "--apply" in sys.argv
    rows = json.load(open(BACKUP))
    ddb = boto3.client("dynamodb", region_name=REGION)
    for item in rows:
        sym = item["symbol"]["S"]
        print(f"{sym}: restore alpha_vantage row ({item['source_symbol']['S']}), delete yahoo_finance row" + ("" if apply else "  [dry run]"))
        if apply:
            ddb.put_item(TableName=TABLE, Item=item)
            ddb.delete_item(TableName=TABLE, Key={"source": {"S": "yahoo_finance"}, "symbol": {"S": sym}})
    if not apply:
        print("DRY RUN: nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

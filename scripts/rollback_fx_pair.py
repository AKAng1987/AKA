"""
rollback_fx_pair.py <SYMBOL> — revert one FX pair from alpha_vantage
back to fred. One-command recovery for a specific pair's AV cron
failing repeatedly post-migration.

Usage: python3 rollback_fx_pair.py USDJPY
"""
import sys
import boto3

REGION = "ap-southeast-1"
TABLE = "cmon-stage-backend-metrics-source"

# Original fred source_symbol + invert, from the pre-migration state
# (PHASE_FX_SWAP.md "Scope" table) -- needed to restore exactly, not
# approximately.
ORIGINAL_FRED = {
    "USDCNY": ("DEXCHUS", False), "USDHKD": ("DEXHKUS", False),
    "USDINR": ("DEXINUS", False), "USDJPY": ("DEXJPUS", False),
    "USDKRW": ("DEXKOUS", False), "USDSGD": ("DEXSIUS", False),
    "USDCHF": ("DEXSZUS", False), "USDTHB": ("DEXTHUS", False),
    "USDAUD": ("DEXUSAL", True),  "USDEUR": ("DEXUSEU", True),
    "USDGBP": ("DEXUSUK", True),
}


def main():
    symbol = sys.argv[1]
    source_symbol, invert = ORIGINAL_FRED[symbol]
    ddb = boto3.client("dynamodb", region_name=REGION)
    fred_item = {
        "source": {"S": "fred"}, "symbol": {"S": symbol},
        "source_symbol": {"S": source_symbol},
    }
    if invert:
        fred_item["invert"] = {"BOOL": True}
    ddb.transact_write_items(TransactItems=[
        {"Delete": {"TableName": TABLE, "Key": {
            "source": {"S": "alpha_vantage"}, "symbol": {"S": symbol},
        }}},
        {"Put": {"TableName": TABLE, "Item": fred_item}},
    ])
    print(f"{symbol}: reverted alpha_vantage -> fred ({source_symbol}, invert={invert})")


if __name__ == "__main__":
    main()

"""
etf_constituents_build.py -- which companies each Fundamentals theme is made of (the top holdings of its ETFs). Zero tokens, run on this Mac.

  python etf_constituents_build.py            # build and upload to the cache object the API reads (Cache/macro/etf_constituents.json)
  python etf_constituents_build.py --dry-run  # build and print; upload nothing

WHY THIS EXISTS (2026-11-23): the API derived these on a request, cached for 7 days. SEC's browse-edgar series lookup began answering
400 for every ETF on 2026-11-22, the 7-day cache expired the same day, the rebuild returned nothing for most themes and the Fundamentals
tab fell from 175 companies / 43 themes to 99 / 24 (the earnings calendar and the filings job use the same universe). The last good copy
was restored from S3 versioning. This job builds the same object offline with the fixed N-PORT lookup (api/etf_holdings.py: registrant
submissions list instead of browse-edgar) and REFUSES to upload a result with fewer than 80% of the previous object's themes filled, so a
bad day cannot overwrite a good copy again.

The SEC user agent is NOT in this repo: it comes from SEC_USER_AGENT or the first line of ~/.cgi/sec_user_agent.txt.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

API = os.path.expanduser("~/cgi-vercel/api")
sys.path.insert(0, API)
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
KEY = "Cache/macro/etf_constituents.json"
REGION = "ap-southeast-1"
UA_FILE = os.path.expanduser("~/.cgi/sec_user_agent.txt")
ACC_FILE = os.path.expanduser("~/.cgi/nport_series.json")
TOP_PER_THEME = 5
MIN_SHARE_OF_PREVIOUS = 0.8


def load_ua() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua and os.path.exists(UA_FILE):
        ua = open(UA_FILE).readline().strip()
    if not ua:
        raise SystemExit("No SEC user agent. Create ~/.cgi/sec_user_agent.txt (one line: a client name plus your contact) or set SEC_USER_AGENT.")
    return ua


def filled(obj: dict | None) -> int:
    return sum(1 for v in ((obj or {}).get("constituents") or {}).values() if v)


def build(eh, td, skip: set) -> dict:
    out, prov = {}, {}
    for theme, proxies in td.THEMES.items():
        tickers, p = eh.top_constituents(proxies, n=20, min_weight=0.5)
        out[theme] = [t for t in tickers if t not in skip][:TOP_PER_THEME]
        prov[theme] = p
        print(f"  {theme:28s} {len(out[theme])} names", flush=True)
    return {"as_of": dt.date.today().isoformat(), "top_per_theme": TOP_PER_THEME, "constituents": out, "provenance": prov}


def main() -> int:
    dry = "--dry-run" in sys.argv
    import etf_holdings as eh
    import themes_data as td

    eh.UA = load_ua()
    try:
        eh._ACC_SERIES.update(json.load(open(ACC_FILE)))
    except Exception:  # noqa: BLE001
        pass
    import boto3

    s3 = boto3.client("s3", region_name=REGION)
    prev = None
    try:
        prev = json.loads(s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read())
    except Exception:  # noqa: BLE001
        pass
    skip: set = set()
    try:
        skip = set(json.loads(s3.get_object(Bucket=BUCKET, Key="State/unreachable_symbols.json")["Body"].read()).get("symbols", []))
    except Exception:  # noqa: BLE001
        pass
    obj = build(eh, td, skip)
    json.dump(eh._ACC_SERIES, open(ACC_FILE, "w"))
    n_new, n_prev, n_themes = filled(obj), filled(prev), len(td.THEMES)
    print(f"themes filled: {n_new} of {n_themes} (previous object: {n_prev})")
    if dry:
        return 0
    if n_prev and n_new < MIN_SHARE_OF_PREVIOUS * n_prev:
        print("REFUSING to upload: fewer than 80% of the previous object's themes are filled (the SEC feed may have changed again)")
        return 1
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=json.dumps(obj, separators=(",", ":")).encode(), ContentType="application/json")
    print("uploaded", KEY)
    return 0


if __name__ == "__main__":
    sys.exit(main())

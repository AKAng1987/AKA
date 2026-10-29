"""Build the S&P 500 capex snapshot from SEC filings and store it for the CGI API.

Run from ~/cgi-vercel with its venv (the logic lives in api/capex_data.py):

    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --out /tmp/capex.json     # build, write a local file only
    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --upload                  # build and store to S3
    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --from /tmp/capex.json --upload   # store a file you already checked
    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --universe                # list the companies, build nothing

Re-run in earnings seasons (late Jan, Apr, Jul, Oct, and again two weeks later for
late filers). It is safe to run any time: the snapshot is replaced whole, and the
API reads it with a 24h cache. About 500 SEC requests, paced; roughly 8 minutes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

API = os.path.expanduser("~/cgi-vercel/api")
sys.path.insert(0, API)

import capex_data as cd  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write the snapshot to this local file")
    ap.add_argument("--upload", action="store_true", help="store the snapshot to S3 for the API")
    ap.add_argument("--from", dest="src", help="use this snapshot file instead of building")
    ap.add_argument("--universe", action="store_true", help="print the company list and stop")
    a = ap.parse_args()

    if a.universe:
        uni = cd.universe()
        by = {}
        for u in uni:
            by.setdefault(u["sector"], []).append(u["ticker"])
        for s in cd.SECTORS:
            print(f"{s:26s} {len(by.get(s, [])):3d}")
        print(f"{'total':26s} {len(uni):3d}")
        return 0

    if a.src:
        snap = json.load(open(a.src))
    else:
        print("building from SEC filings ...")
        snap = cd.build_snapshot()
    n = sum(1 for c in snap["companies"] if c["cq"])
    print(f"{len(snap['companies'])} companies read, {n} with capex, {len(snap['failed'])} failed {snap['failed'][:12]}")
    if a.out:
        json.dump(snap, open(a.out, "w"), separators=(",", ":"))
        print("wrote", a.out)
    if a.upload:
        if n < 300:
            print(f"refusing to upload: only {n} companies have capex (expect 400+)")
            return 1
        cd.write_snapshot(snap)
        print("stored to S3:", cd.KEY)
    return 0


if __name__ == "__main__":
    sys.exit(main())

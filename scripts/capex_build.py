"""Build the S&P 500 capex snapshot from SEC filings and store it for the CGI API.

Run from ~/cgi-vercel with its venv (the logic lives in api/capex_data.py):

    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --out /tmp/capex.json     # build, write a local file only
    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --upload                  # build and store to S3
    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --from /tmp/capex.json --upload   # store a file you already checked
    api/.venv/bin/python ~/market-dashboard/scripts/capex_build.py --universe                # list the companies, build nothing

Runs weekly, unattended, from launchd (~/Library/LaunchAgents/com.arvin.capex-build.plist, Sunday 09:00 Manila,
so Monday's page is fresh; a Mac that was asleep runs it on wake). Safe to run any time: the snapshot is replaced
whole, a build that lost companies is refused, and the API reads it with a 24h cache. About 500 SEC requests,
paced; roughly 8 minutes. If a week is missed, /api/freshness shows a `capex` problem after 9 days.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

API = os.path.expanduser("~/cgi-vercel/api")
sys.path.insert(0, API)

import capex_data as cd  # noqa: E402


def _fail(msg: str) -> int:
    """Say it out loud: this runs unattended from launchd, where a silent failure would only show as 'snapshot old'."""
    print(msg)
    if a_notify:
        text = msg[:120].replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "CGI capex refresh"'], check=False)
    return 1


a_notify = False


def main() -> int:
    global a_notify
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write the snapshot to this local file")
    ap.add_argument("--upload", action="store_true", help="store the snapshot to S3 for the API")
    ap.add_argument("--from", dest="src", help="use this snapshot file instead of building")
    ap.add_argument("--notify", action="store_true", help="raise a macOS notification on failure (for launchd)")
    ap.add_argument("--universe", action="store_true", help="print the company list and stop")
    a = ap.parse_args()
    a_notify = a.notify

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
            return _fail(f"refusing to upload: only {n} companies have capex (expect 400+)")
        old = cd.read_snapshot()
        if old:
            n_old = sum(1 for c in old["companies"] if c["cq"])
            if n < n_old * 0.95:
                return _fail(f"refusing to upload: {n} companies with capex, the stored snapshot has {n_old}")
        cd.write_snapshot(snap)
        print("stored to S3:", cd.KEY)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 -- unattended: report any crash, never fail silently
        sys.exit(_fail(f"capex refresh crashed: {type(e).__name__}: {e}"))

"""
cgi-keep-awake -- keeps the free Render API awake, from AWS, on time.

Why: the GitHub workflow that was meant to ping every 10 minutes ran about 3 times a day (GitHub drops and delays frequent
scheduled runs), so the free API slept most of the day and every first visitor paid a cold start of up to ~75 s.
EventBridge fires on time and Lambda's always-free tier covers this (about 4,400 invocations a month).

Every run: GET the API root. A non-2xx/3xx answer or no answer RAISES, so CloudWatch counts an Error and the alarm can mail.
Once an hour (the run that starts in minutes 0-9 UTC): also visit the heavy pages one at a time, 8 s apart, so a cache rebuild
lands here and not on a visitor. A slow or failed warm-up never fails the run.
Environment: API_URL (the Render address; a public URL, not a secret), SITE_URL (default the Vercel site).
Stdlib only. Read-only: it only does GET requests.
"""
from __future__ import annotations

import datetime as dt
import os
import time
import urllib.error
import urllib.request

WARM_PATHS = ["/api/freshness", "/api/watchlists", "/api/themes", "/", "/countries", "/philippines", "/backtest", "/fundamentals"]
ROOT_TIMEOUT = 90
WARM_TIMEOUT = 70
WARM_PAUSE = 8


def _get(url: str, timeout: int) -> tuple[str, float]:
    """(status text, seconds). Never raises for an HTTP error status: returns it."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cgi-keep-awake"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
            return str(r.status), time.time() - t0
    except urllib.error.HTTPError as e:
        return str(e.code), time.time() - t0
    except Exception as e:  # noqa: BLE001
        return f"no response ({type(e).__name__})", time.time() - t0


def should_warm(now: dt.datetime) -> bool:
    return now.minute < 10


def lambda_handler(event, context):
    api = os.environ["API_URL"].rstrip("/")
    site = os.environ.get("SITE_URL", "https://cgi-vercel.vercel.app").rstrip("/")
    code, secs = _get(api + "/", ROOT_TIMEOUT)
    print(f"API root answered: {code} in {secs:.1f}s")
    warmed = []
    if should_warm(dt.datetime.now(dt.timezone.utc)):
        for p in WARM_PATHS:
            c, s = _get(site + p, WARM_TIMEOUT)
            print(f"warm {p} -> {c} {s:.1f}s")
            warmed.append((p, c))
            time.sleep(WARM_PAUSE)
    if not (code.startswith("2") or code.startswith("3")):
        raise RuntimeError(f"API root answered: {code}")
    return {"api": code, "seconds": round(secs, 1), "warmed": len(warmed)}

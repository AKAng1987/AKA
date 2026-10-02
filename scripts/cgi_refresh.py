"""
cgi_refresh.py -- the deterministic half of the daily manual-series refresh.

WHY THIS EXISTS

The refresh routine has to be an LLM session, because the symbols it keeps
current come from TradingView and TradingView has no server-side API -- it is
reachable only through an MCP, and only a model can call one. That makes the
routine the one part of CGI that stops when the token budget does, which it
did on 2026-10-01.

Tokens cannot be removed from the loop, but almost everything the loop does
is deterministic: read the worklist, decide what is genuinely new, post it,
report. Only the fetch needs the model. So the model now runs two commands
and makes the MCP calls in between, instead of reasoning its way through
fifty-six symbols.

  plan   -- print the symbols that need a source check, with their tickers
  apply  -- read fetched bars on stdin, post only what is new, heartbeat

USAGE (from the scheduled task)

  python3 scripts/cgi_refresh.py plan
  # ... model fetches each listed ticker via the TradingView MCP ...
  echo '{"HIGQ": [["2026-10-01", 41]], ...}' | python3 scripts/cgi_refresh.py apply

`apply` takes {SYMBOL: [[ISO date, value], ...]} -- whatever was fetched,
including bars we already hold. It works out what is new; the API rejects
anything at or before the last stored row anyway, so re-sending is safe.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("CGI_API_URL", "https://cgi-api-9mim.onrender.com").rstrip("/")
TIMEOUT = 180
MAX_ROWS_PER_CALL = 24          # the endpoint's own cap


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{API}{path}", timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{API}{path}", method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


# Days between checks, by cadence. A series is re-checked when its source
# could plausibly have published again -- not when our copy got old.
#
# The difference matters enormously. PHCBBS has been CORRECT and 243 days
# old since BSP stopped publishing; judged on the age of our data it is
# overdue every day forever, and the routine would fetch all 56 symbols
# daily to learn nothing. Judged on when we last ASKED the source, it is
# checked once a month like any other monthly series.
#
# That is the difference between ~56 fetches a day and ~12.
CHECK_EVERY_DAYS = {"daily": 1, "weekly": 7, "monthly": 31,
                    "quarterly": 92, "annual": 366, "unknown": 7}


def _state() -> dict:
    """Per-symbol record of when we last asked the source, from the heartbeat."""
    try:
        return (_get("/api/freshness").get("refresh_routine") or {}).get("ran_detail") or {}
    except Exception:  # noqa: BLE001 -- no state means check everything, which is safe
        return {}


def plan() -> int:
    """What to fetch. Everything else the model would have had to work out."""
    f = _get("/api/freshness")
    checked = _state()
    today = dt.date.today()
    out, skipped = [], 0
    for r in f["manual"]["series"]:
        sym = r["symbol"]
        every = CHECK_EVERY_DAYS.get(r.get("cadence") or "unknown", 7)
        last = checked.get(sym, {}).get("at")
        if r["action"] != "load" and last:
            try:
                if (today - dt.date.fromisoformat(last)).days < every:
                    skipped += 1
                    continue
            except ValueError:
                pass
        out.append({"symbol": sym, "fetch": r["source_symbol"],
                    "we_hold": r["ours_date"], "cadence": r["cadence"]})
    print(json.dumps({"api": API, "n": len(out), "skipped_checked_recently": skipped,
                      "fetch": out}, indent=1))
    if not out:
        print("\n# nothing due -- still run `apply` with {} to record the heartbeat",
              file=sys.stderr)
    return 0


def apply_() -> int:
    """Post what is genuinely new, then report. Never invents a bar."""
    try:
        fetched = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"stdin was not JSON: {exc}", file=sys.stderr)
        return 2

    held = {r["symbol"]: r["ours_date"]
            for r in _get("/api/freshness")["manual"]["series"]}

    checked = _state()
    today = dt.date.today().isoformat()
    updated, current, failed = [], [], []
    for sym, bars in sorted(fetched.items()):
        # Record that we ASKED, and what the source said, whether or not
        # anything was new. This is what stops a correct-but-old series
        # being re-fetched every day.
        newest = max((d for d, _ in bars), default=None)
        checked[sym] = {"at": today, "source_newest": newest}
        last = held.get(sym)
        rows = sorted(
            ({"date": d, "value": float(v)} for d, v in bars
             if last is None or d > last),
            key=lambda r: r["date"],
        )
        if not rows:
            current.append(sym)
            continue
        try:
            for i in range(0, len(rows), MAX_ROWS_PER_CALL):
                res = _post(f"/api/series/{sym}", {"rows": rows[i:i + MAX_ROWS_PER_CALL]})
            updated.append(f"{sym}->{res['last_date_after']}({res['written']})")
        except urllib.error.HTTPError as exc:
            failed.append(f"{sym}: HTTP {exc.code} {exc.read().decode()[:90]}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{sym}: {type(exc).__name__} {exc}")

    summary = (f"{len(updated)} updated, {len(current)} already current, "
               f"{len(failed)} failed")
    if updated:
        summary += " | " + ", ".join(updated[:12])
    if failed:
        summary += " | FAILED: " + "; ".join(failed[:5])

    # The heartbeat records that the routine RAN, not that everything
    # succeeded -- a run that found three failures is still a live routine,
    # and the failures are reported separately. Only a run that never
    # happens should read as dead.
    try:
        _post("/api/freshness/heartbeat", {"summary": summary, "checked": checked})
    except Exception as exc:  # noqa: BLE001
        print(f"heartbeat failed: {exc}", file=sys.stderr)

    print(summary)
    for f_ in failed:
        print("  FAIL", f_, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        raise SystemExit(plan())
    if cmd == "apply":
        raise SystemExit(apply_())
    raise SystemExit("usage: cgi_refresh.py plan | apply")

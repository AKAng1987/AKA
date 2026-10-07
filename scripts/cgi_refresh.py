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
  referee-- print the ~15 benchmarks to fetch from TradingView as a second source
  apply  -- read fetched bars on stdin, post only what is new, heartbeat (with "_referee" bars, if fetched)

  lists plan             -- compare /api/watchlists with what was last written to the three
                            TradingView lists; print remove/add only for lists that differ
  lists done ID=HASH ... -- record that those lists were written (refuses if the API moved)
  lists verify           -- weekly: stdin {id: [symbols TradingView holds]}; reports drift

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
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

# Through VERCEL, not Render. This routine runs on the user's own machine, and the networks
# that machine sits on sinkhole *.onrender.com (DNS answers with a private 192.168.x address)
# while vercel.app stays reachable. The first weekday run, 2026-10-05, failed with "Network
# is unreachable" on every call and wrote no heartbeat. Every endpoint used here has a
# same-origin proxy under web/app/api: /api/freshness, /api/freshness/heartbeat,
# /api/series/<symbol> and /api/watchlists.
API = os.environ.get("CGI_API_URL", "https://cgi-vercel.vercel.app").rstrip("/")
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

# ...but "once a month since we last asked" can miss a print by a whole month: ISM services was asked on
# 2026-10-03, printed on 2026-10-05, and would not have been asked again until ~2026-11-03. So each series is
# matched to its release calendar: while its NEXT print is normally due it is checked every run until it lands,
# then it goes quiet. The 31/92-day rule above stays as the backstop for anything that misses its window.
#
# Windows are (first, last) days after the NEXT period's stamp date (a September print is stamped 2026-09-01),
# taken from each source's usual schedule with a few days' margin either side. Policy rates move only at
# meetings and are left to the backstop.
RELEASE_WINDOW = {
    # US surveys
    "ISM_MFG_PMI": (29, 36), "ISM_MFG_PRICES": (29, 36),          # 1st business day of next month
    "ISM_SVC_ACTIVITY": (31, 38), "ISM_SVC_PRICES": (31, 38),     # 3rd business day of next month
    "CHALLENGER": (29, 40),                                       # first week of next month
    # CPI
    "KR_CPI_YOY": (29, 35),   # 1st-2nd of next month
    "EU_CPI_YOY": (28, 35),   # flash, last day of the month / 1st of next
    "PH_CPI_YOY": (32, 39),   # ~5th of next month
    "CN_CPI_YOY": (36, 45),   # ~9th-11th of next month
    "GB_CPI_YOY": (44, 53),   # ~3rd Wednesday of next month
    "JP_CPI_YOY": (46, 55),   # ~3rd Friday of next month
    # Money and credit
    "JP_M2": (36, 43), "JP_LOAN_GROWTH_YOY": (36, 43),            # ~8th-10th of next month
    "CN_M2": (37, 48), "CN_LOAN_GROWTH_YOY": (37, 48),            # ~10th-15th of next month
    "EU_M2": (54, 63), "EU_LOAN_GROWTH_YOY": (54, 63),            # ~27th-29th of next month
    "GB_M2": (56, 64),                                            # ~end of next month
    "PH_M2": (54, 64), "PH_LOANS_PRIVATE": (54, 64),              # ~end of next month
    "KR_M2": (68, 82), "KR_LOANS_PRIVATE": (68, 82),              # ~mid month+2
    # Central bank balance sheets
    "CN_CB_ASSETS": (48, 66), "KR_CB_ASSETS": (68, 82),
    # GDP (quarterly; next stamp = 3 months on)
    "CN_GDP_YOY": (44, 53),   # ~mid month after quarter end
    "KR_GDP_YOY": (51, 60),   # advance, ~4 weeks after quarter end
    "EU_GDP_YOY": (57, 65),   # flash, ~30 days after quarter end
    "PH_GDP_YOY": (63, 72),   # ~5-6 weeks after quarter end
    "GB_GDP_YOY": (70, 80), "JP_GDP_YOY": (70, 80),               # ~mid second month after quarter end
}


def _in_release_window(sym: str, ours_date: str | None, today: dt.date, cadence: str = "monthly") -> bool:
    win = RELEASE_WINDOW.get(sym)
    if not win or not ours_date:
        return False
    d = dt.date.fromisoformat(ours_date).replace(day=1)
    months = 3 if cadence == "quarterly" else 1
    y, m = divmod(d.month - 1 + months, 12)
    next_stamp = dt.date(d.year + y, m + 1, 1)
    return win[0] <= (today - next_stamp).days <= win[1]


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
        if r["action"] != "load" and last and not _in_release_window(sym, r.get("ours_date"), today, r.get("cadence") or "monthly"):
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


# The referee: ~15 benchmarks the morning run also fetches from TradingView (a second vendor), sent with the
# heartbeat and compared server-side against what is stored (/api/freshness -> "referee"). Symbols are the
# verified ones from api/tv_symbols; keep in step with REFEREE in api/freshness.py.
REFEREE = {
    "SPX": "TVC:SPX", "DJI": "TVC:DJI", "IXIC": "NASDAQ:IXIC", "RUT": "TVC:RUT", "SPY": "AMEX:SPY",
    "QQQ": "NASDAQ:QQQ", "DBA": "AMEX:DBA", "UUP": "AMEX:UUP", "MAGS": "CBOE:MAGS", "VIX": "TVC:VIX",
    "USOIL": "NYMEX:CL1!", "XAUUSD": "COMEX:GC1!", "NATGAS": "NYMEX:NG1!", "US10Y": "TVC:US10Y", "USDJPY": "FX:USDJPY",
}


def _trade_date(t: int) -> str:
    """TradingView stamps a daily bar at its session OPEN. Futures, FX and Treasury sessions open the evening
    before (18:00 ET), so the trading date is the New York date 8 hours after the stamp."""
    from zoneinfo import ZoneInfo
    return dt.datetime.fromtimestamp(int(t) + 8 * 3600, ZoneInfo("America/New_York")).date().isoformat()


def referee_plan() -> int:
    print(json.dumps({"fetch_with": "mcp-tv-get-ohlcv interval=1D count=5",
                      "send_in_apply_as": '"_referee": {SYMBOL: [[t, c], ...]}',
                      "symbols": REFEREE}, indent=1))
    return 0


def apply_() -> int:
    """Post what is genuinely new, then report. Never invents a bar."""
    try:
        fetched = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"stdin was not JSON: {exc}", file=sys.stderr)
        return 2

    raw_ref = fetched.pop("_referee", None) or {}
    referee = {}
    for sym, bars in raw_ref.items():
        if sym in REFEREE and isinstance(bars, list):
            try:
                referee[sym] = [[_trade_date(b[0]), float(b[1])] for b in bars[-5:]]
            except (TypeError, ValueError, IndexError):
                print(f"referee: could not read bars for {sym}", file=sys.stderr)

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
        _post("/api/freshness/heartbeat", {"summary": summary, "checked": checked, "referee": referee})
    except Exception as exc:  # noqa: BLE001
        print(f"heartbeat failed: {exc}", file=sys.stderr)

    print(summary)
    for f_ in failed:
        print("  FAIL", f_, file=sys.stderr)
    return 1 if failed else 0


# ── TradingView list sync: write only on change ─────────────────────────────
#
# The three CGI watchlists change a few times a month, so the routine must not re-write
# them daily. The API cannot see TradingView, so this keeps a small local record of what
# was LAST WRITTEN and diffs the API's desired lists against it: nothing changed means no
# TradingView call at all. The model only runs the remove/add arrays printed here.
#
# The tools do not replace a list, so a rewrite is remove(everything we last wrote) then
# add(desired) -- not atomic, which is why `verify` exists: weekly, the model reads the lists
# back and drift (a manual edit, an interrupted rewrite) is caught and repaired.
STATE_PATH = pathlib.Path(os.environ.get("CGI_LISTS_STATE", "~/.cgi/tv_lists_state.json")).expanduser()


def _lists_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_lists_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1))


def _hash(symbols: list[str]) -> str:
    return hashlib.sha1("\n".join(symbols).encode()).hexdigest()[:10]


def diff_lists(desired: dict[str, list[str]], state: dict) -> list[dict]:
    """Pure: per list, what to do. `desired` = {id: symbols}; `state` = {id: {symbols}}."""
    out = []
    for lid, want in desired.items():
        have = (state.get(lid) or {}).get("symbols")
        if have is None:
            out.append({"id": lid, "action": "read_first", "hash": _hash(want),
                        "why": "no record of what was written; get_watchlist, then `lists verify`"})
        elif have == want:
            out.append({"id": lid, "action": "unchanged"})
        else:
            out.append({"id": lid, "action": "rewrite", "hash": _hash(want),
                        "remove": have, "add": want})
    return out


def lists_plan() -> int:
    desired = {str(w["watchlist_id"]): w["symbols"] for w in _get("/api/watchlists")["watchlists"]}
    plan_ = diff_lists(desired, _lists_state())
    changed = [p for p in plan_ if p["action"] != "unchanged"]
    print(json.dumps({"any_change": bool(changed), "lists": plan_}, indent=1, ensure_ascii=False))
    if not changed:
        print("\n# all three lists match what was last written -- no TradingView call needed",
              file=sys.stderr)
    return 0


def lists_done(args: list[str]) -> int:
    """Record that lists were written. Refuses if the API's lists have moved since `plan`, so
    nothing is ever recorded as written that the model was not actually told to write."""
    desired = {str(w["watchlist_id"]): w["symbols"] for w in _get("/api/watchlists")["watchlists"]}
    state, bad = _lists_state(), []
    for a in args:
        lid, _, h = a.partition("=")
        if lid not in desired or _hash(desired[lid]) != h:
            bad.append(lid)
            continue
        state[lid] = {"symbols": desired[lid], "written": dt.date.today().isoformat()}
    _save_lists_state(state)
    if bad:
        print(f"NOT recorded (API changed since plan, or unknown id): {bad} -- re-run `lists plan`",
              file=sys.stderr)
        return 1
    print("recorded:", ", ".join(a.split("=")[0] for a in args))
    return 0


def lists_verify() -> int:
    """stdin {id: [symbols TradingView holds now]}. Records reality, and reports drift."""
    try:
        held = {str(k): v for k, v in json.loads(sys.stdin.read() or "{}").items()}
    except json.JSONDecodeError as exc:
        print(f"stdin was not JSON: {exc}", file=sys.stderr)
        return 2
    desired = {str(w["watchlist_id"]): w["symbols"] for w in _get("/api/watchlists")["watchlists"]}
    state, out = _lists_state(), []
    for lid, now in held.items():
        want = desired.get(lid)
        prior = (state.get(lid) or {}).get("symbols")
        row = {"id": lid, "matches_desired": now == want, "matches_last_written": now == prior}
        if now != want and want is not None:
            row.update(action="rewrite", hash=_hash(want), remove=now, add=want)
        else:
            row["action"] = "none"
        out.append(row)
        state[lid] = {"symbols": now, "verified": dt.date.today().isoformat()}   # reality, not belief
    _save_lists_state(state)
    print(json.dumps({"drift": any(r["action"] != "none" for r in out), "lists": out},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "referee":
        raise SystemExit(referee_plan())
    if cmd == "plan":
        raise SystemExit(plan())
    if cmd == "apply":
        raise SystemExit(apply_())
    if cmd == "lists":
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        if sub == "plan":
            raise SystemExit(lists_plan())
        if sub == "done":
            raise SystemExit(lists_done(sys.argv[3:]))
        if sub == "verify":
            raise SystemExit(lists_verify())
    raise SystemExit("usage: cgi_refresh.py plan | apply | lists plan | lists done ID=HASH... | lists verify")

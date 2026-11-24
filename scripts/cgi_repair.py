"""
cgi_repair.py -- zero-token auto-repair for CGI. Runs after each data batch lands (launchd com.arvin.cgi-repair).

  python3 scripts/cgi_repair.py             # check, repair what has a playbook entry, re-check, log, notify
  python3 scripts/cgi_repair.py --dry-run   # classify and print the plan; take no action
  python3 scripts/cgi_repair.py --pending   # print open faults that need Claude (class claude) or his yes (class code), as JSON

Each fault from `cgi_health.py --json` goes in one class:
  wait    the provider is late or nothing on our side can help. Logged, re-checked next slot.
  fix     a playbook entry exists: run it, then re-check.
  claude  data work that needs TradingView or reading (the app task cgi-repair picks these up).
  code    needs a code change: prepared and tested by the app task, pushed only on his yes.

Limits: never overwrites stored history (only tools that insert-if-absent or rebuild derived data); never touches Pulse, brokers
or orders; at most MAX_ATTEMPTS tries per fault per day, then it becomes class claude; stops at once when the pause lever exists
(~/handoffs/runs/PAUSE). Every action goes to events.jsonl. Plain stdlib; no model.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOME = os.path.expanduser("~")
PAUSE = os.path.join(HOME, "handoffs/runs/PAUSE")
STATE = os.path.join(HOME, ".cgi/repair_state.json")
API_PY = os.path.join(HOME, "cgi-vercel/api/.venv/bin/python")
API_DIR = os.path.join(HOME, "cgi-vercel/api")
MAX_ATTEMPTS = 2
SETTLE_S = 120          # time given to an asynchronous Lambda before re-checking
P = "cmon-stage-backend-"
LAMBDAS = {"derived": P + "derived-metrics-updater", "dashboard": P + "dashboard", "yahoo": P + "yahoo-finance-updater",
           "keepawake": "cgi-keep-awake"}


# ---- classification (pure; tested in check_cgi_repair.py) -------------------------------------------------------------------

def classify(text: str, prior_attempts: int = 0) -> tuple[str, str]:
    """(class, playbook key) for one line of cgi_health's red/amber list."""
    t = text.strip()
    low = t.lower()
    if low.startswith(("site unreachable", "could not read brief", "could not read tape")):
        return "fix", "wake"
    if low.startswith("cot"):
        return "wait", "provider_late"
    if low.startswith("refresh routine") or low.startswith("referee received no"):
        return "claude", "refresh_routine"
    if low.startswith("referee"):
        return "claude", "referee_mismatch"      # reported and diagnosed, never fixed by overwriting a price
    if low.startswith("pipeline") or low.startswith("tape "):
        return "fix", "derived_chain"
    if low.startswith("stalled"):
        if "yahoo" in low:
            # one re-invoke; if the provider still has no bar the feed is waiting on the provider, not on us
            return ("wait", "provider_late") if prior_attempts >= 1 else ("fix", "yahoo_rerun")
        return "claude", "stalled_feed"
    if low.startswith("capex"):
        return "fix", "capex_build"
    if low.startswith("ph "):
        return "fix", "ph_daily"
    if low.startswith("release calendar"):
        return "code", "calendar_dates"
    if low.startswith("price jump"):
        return "code", "split_seam"           # rewrites stored prices (fix_split_seams.py): held for Arvin's yes, never auto-run
    if low.startswith("fred "):
        return "wait", "provider_late"        # the nightly FRED copy catches up on its own; health keeps showing it until it does
    if low.startswith("gdp"):
        return "claude", "gdp"
    if low.startswith("rate path"):
        return ("claude", "rate_path") if prior_attempts >= 1 else ("wait", "provider_late")
    return "claude", "unknown"


def fault_key(text: str) -> str:
    return re.sub(r"\d{4}-\d{2}-\d{2}|\d+(\.\d+)?", "#", text.strip().lower())[:90]


def plan(faults: list[str], state_today: dict) -> list[dict]:
    out = []
    for text in faults:
        k = fault_key(text)
        tries = int(state_today.get(k, {}).get("attempts", 0))
        cls, play = classify(text, tries)
        if cls == "fix" and tries >= MAX_ATTEMPTS:
            cls, play = "claude", play + "_gave_up"
        out.append({"text": text, "key": k, "class": cls, "play": play, "attempts": tries})
    return out


# ---- actions ---------------------------------------------------------------------------------------------------------------

def sh(cmd: list[str], timeout: int = 900, cwd: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout + r.stderr)[-400:]
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)[:200]


def invoke(name: str) -> bool:
    rc, _ = sh(["aws", "lambda", "invoke", "--function-name", name, "--invocation-type", "Event", "/dev/null"], 60)
    return rc == 0


def drop_cache(key: str) -> bool:
    code = f"import sys; sys.path.insert(0, {API_DIR!r}); import cache; print(cache.invalidate({key!r}))"
    rc, _ = sh([API_PY, "-I", "-c", code], 120, cwd=API_DIR)
    return rc == 0


def run_play(play: str) -> str:
    """Run one playbook entry. Returns a short plain description of what was done."""
    if play == "wake":
        ok = invoke(LAMBDAS["keepawake"])
        time.sleep(90)
        return "woke the API" if ok else "could not invoke keep-awake"
    if play == "derived_chain":
        ok1 = invoke(LAMBDAS["derived"])
        time.sleep(SETTLE_S)
        ok2 = invoke(LAMBDAS["dashboard"])
        time.sleep(SETTLE_S)
        drop_cache("live")
        return "re-ran derived metrics then dashboard, dropped the live cache" if ok1 and ok2 else "a Lambda invoke failed"
    if play == "yahoo_rerun":
        ok = invoke(LAMBDAS["yahoo"])
        time.sleep(SETTLE_S)
        return "re-ran the Yahoo updater" if ok else "could not invoke the Yahoo updater"
    if play == "capex_build":
        rc, out = sh([API_PY, "-u", os.path.join(HERE, "capex_build.py"), "--upload"], 3000, cwd=os.path.join(HOME, "cgi-vercel"))
        return "re-ran the capex build" if rc == 0 else f"capex build exited {rc}"
    if play == "ph_daily":
        rc, _ = sh(["/bin/bash", os.path.join(HERE, "ph_daily.sh")], 3000)
        return "re-ran the PH daily job" if rc == 0 else f"PH job exited {rc}"
    return "no action"


# ---- plumbing --------------------------------------------------------------------------------------------------------------

def load_state() -> dict:
    try:
        s = json.load(open(STATE))
    except Exception:  # noqa: BLE001
        s = {}
    today = dt.date.today().isoformat()
    return s if s.get("day") == today else {"day": today, "faults": {}}


def save_state(s: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=1)


def health() -> dict:
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "cgi_health.py"), "--json"], capture_output=True, text=True, timeout=400)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "RED", "red": [f"site unreachable: {exc}"], "amber": []}


def log(level: str, kind: str, text: str, key: str) -> None:
    try:
        import events as EV
        EV.log_event("cgi_repair", kind, level, text, key=f"cgi_repair-{kind}:{key}:{dt.datetime.now().strftime('%Y-%m-%d-%H')}")
    except Exception:  # noqa: BLE001
        pass


def notify(msg: str) -> None:
    sh(["osascript", "-e", f'display notification "{msg[:180]}" with title "CGI auto-repair"'], 15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pending", action="store_true")
    a = ap.parse_args()

    if a.pending:
        st = load_state()
        print(json.dumps([{"text": v["text"], "class": v["class"], "play": v["play"]} for v in st.get("open", [])
                          if v["class"] in ("claude", "code")]))
        return 0
    if os.path.exists(PAUSE) and not a.dry_run:
        print("paused: " + open(PAUSE).read().strip())
        return 0

    st = load_state()
    h = health()
    faults = h.get("red", []) + h.get("amber", [])
    if not faults:
        st["open"] = []
        save_state(st)
        print("GREEN: nothing to repair")
        return 0

    items = plan(faults, st.setdefault("faults", {}))
    for it in items:
        print(f"{it['class']:6} {it['play']:18} {it['text'][:100]}")
    if a.dry_run:
        return 0

    fixes = [i for i in items if i["class"] == "fix"]
    for plays in sorted({i["play"] for i in fixes}):
        done = run_play(plays)
        for i in fixes:
            if i["play"] == plays:
                rec = st["faults"].setdefault(i["key"], {"attempts": 0})
                rec["attempts"] += 1
        log("info", "action", f"Auto-repair: {done}", plays)

    after = health() if fixes else h
    still = set(fault_key(x) for x in after.get("red", []) + after.get("amber", []))
    for i in fixes:
        if i["key"] not in still:
            log("info", "fixed", f"Auto-repair fixed: {i['text'][:120]}", i["key"])
            notify("Fixed: " + i["text"][:120])
    for i in items:
        if i["class"] == "wait":
            log("info", "wait", f"Waiting on provider, no action: {i['text'][:120]}", i["key"])
    # what is still open and needs Claude or his yes
    remaining = plan(after.get("red", []) + after.get("amber", []), st["faults"])
    st["open"] = [r for r in remaining if r["class"] in ("claude", "code")]
    for r in st["open"]:
        log("warn", "open", f"Needs {'his yes' if r['class'] == 'code' else 'a Claude run'}: {r['text'][:120]}", r["key"])
    if st["open"]:
        notify(f"{len(st['open'])} fault(s) need attention: {st['open'][0]['text'][:100]}")
    save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())

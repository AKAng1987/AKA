"""
task_watch.py -- zero-token watchdog for the Claude desktop app's scheduled tasks.

WHY: on 2026-10-16..20 the daily video scan and weekday brief were silently skipped. The scheduler refuses to start
a task while an earlier run of it is still "running", and a run that is waiting on a rejected permission prompt
(or whose Mac went to sleep) stays "running" for days. Nothing told anyone. This reads the app's own local session
records, decides, and says so out loud. It only reports; it never stops, starts or edits anything.

  python3 scripts/task_watch.py            # print the verdict
  python3 scripts/task_watch.py --notify   # also raise a macOS notification when something is new
  python3 scripts/task_watch.py --json     # machine-readable, for cgi_health or a page

Per task it reports:
  MISSED   an expected slot passed (+ grace) and no run of that task started since
  LATE     the run did start, but well after its slot (info)
  NOT_AUTO the newest run was not in auto permission mode, so it can stall on a prompt (info)
  STALLED  the newest run never finished and has been idle > STALL_HOURS (it blocks every later slot)
Expected slots are declared in SLOTS below (the cron lives in the app, so keep the two in step).
A run "finished" when its transcript ends with the app's last-prompt/cost-state records.
Plain stdlib; no network.
"""
from __future__ import annotations

import argparse, datetime as dt, glob, json, os, pathlib, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import events as EV
except Exception:  # noqa: BLE001 -- the event log is optional
    EV = None

HOME = pathlib.Path.home()
SESSIONS = HOME / "Library/Application Support/Claude/claude-code-sessions"
STATE = HOME / ".cgi" / "task_watch_state.json"
STALL_HOURS = 2.0

# task id -> (weekdays Mon=0..Sun=6, "HH:MM" local, grace hours). Paused tasks are left out on purpose.
SLOTS = {
    "video-intake-scan":            (range(7), "07:42", 2.0),
    "cgi-daily-brief":              (range(5), "09:10", 2.0),
    "cgi-weekly-decks":             ([6],      "07:01", 3.0),
    "refresh-customer-links-cache": ([6],      "06:41", 3.0),
    "season-desk-daily-refresh":    (range(7), "16:36", 2.0),
    "pulse-mirror-sync":            (range(7), "07:24", 2.0),   # cron 07:10 plus the app's fixed delay
}
LOOKBACK_DAYS = {"daily": 3, "weekly": 8}
FINISHED = {"last-prompt", "cost-state"}


def sessions() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in glob.glob(str(SESSIONS / "*" / "*" / "local_*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        tid = d.get("scheduledTaskId")
        if tid:
            out.setdefault(tid, []).append(d)
    for v in out.values():
        v.sort(key=lambda d: d.get("createdAt", 0))
    return out


def finished(d: dict) -> bool:
    hits = glob.glob(str(HOME / ".claude" / "projects" / "*" / f"{d.get('cliSessionId')}.jsonl"))
    if not hits:
        return True                      # no transcript to judge by: do not cry wolf
    with open(hits[0], "rb") as f:
        f.seek(0, 2); f.seek(max(0, f.tell() - 60000))
        lines = f.read().decode("utf-8", "ignore").splitlines()[-12:]
    for line in lines:
        try:
            if json.loads(line).get("type") in FINISHED:
                return True
        except Exception:
            continue
    return False


def check(now: dt.datetime) -> list[dict]:
    problems, by_task = [], sessions()
    for tid, (days, hhmm, grace) in SLOTS.items():
        runs = by_task.get(tid, [])
        if not runs:
            problems.append({"task": tid, "kind": "NEVER_RUN"}); continue
        weekly = len(list(days)) == 1
        hh, mm = map(int, hhmm.split(":"))
        look = LOOKBACK_DAYS["weekly" if weekly else "daily"]
        # every slot from the lookback start through the next one after now (its start bounds the last window)
        slots = [dt.datetime.combine((now - dt.timedelta(days=look)).date() + dt.timedelta(days=i), dt.time(hh, mm))
                 for i in range(look + 9)]
        slots = [s for s in slots if s.weekday() in days]
        for i, slot in enumerate(slots):
            if now < slot + dt.timedelta(hours=grace):
                break                    # this and every later slot are not due yet
            lo = (slot - dt.timedelta(minutes=30)).timestamp()
            hi = (slots[i + 1] - dt.timedelta(minutes=30)).timestamp() if i + 1 < len(slots) else float("inf")
            # a run only counts for the slot whose window it started in, so one late run cannot paper over several misses
            started = [r for r in runs if lo <= r.get("createdAt", 0) / 1000 < hi]
            if not started:
                problems.append({"task": tid, "kind": "MISSED", "slot": slot.isoformat(timespec="minutes")})
            else:
                lag = started[0]["createdAt"] / 1000 - slot.timestamp()
                if lag > grace * 3600:
                    problems.append({"task": tid, "kind": "LATE", "slot": slot.isoformat(timespec="minutes"),
                                     "started": dt.datetime.fromtimestamp(started[0]["createdAt"] / 1000).isoformat(timespec="minutes")})
        last = runs[-1]
        if last.get("permissionMode") != "auto":    # an unattended run in default mode stalls on its first prompt
            problems.append({"task": tid, "kind": "NOT_AUTO", "mode": last.get("permissionMode"),
                             "started": dt.datetime.fromtimestamp(last["createdAt"] / 1000).isoformat(timespec="minutes")})
        idle_h = (now.timestamp() - last.get("lastActivityAt", 0) / 1000) / 3600
        if idle_h > STALL_HOURS and not finished(last):
            problems.append({"task": tid, "kind": "STALLED", "session": last["sessionId"], "idle_hours": round(idle_h, 1),
                             "since": dt.datetime.fromtimestamp(last["createdAt"] / 1000).isoformat(timespec="minutes")})
    return problems


FANTASY_PULLS = HOME / "Desktop/Arvin/Fantasy NBA Draft/parts/yahoo_pulls"
PULL_MAX_HOURS = 26.0


def pull_age(now: dt.datetime) -> list[dict]:
    """The fantasy page can only be as fresh as its newest Yahoo pull (the app's scheduled run can fail silently, as on 26-28 Oct)."""
    fs = glob.glob(str(FANTASY_PULLS / "roto_league_????-??-??.tsv"))
    if not fs:
        return []                        # folder moved or not set up: do not cry wolf
    hours = (now.timestamp() - max(os.path.getmtime(f) for f in fs)) / 3600
    return [{"task": "fantasy-pulls", "kind": "PULL_OLD", "hours": round(hours, 1)}] if hours > PULL_MAX_HOURS else []


MIRROR_META = HOME / "pulse" / "mirror" / "_meta.json"
MIRROR_MAX_HOURS = 26.0


def mirror_age(now: dt.datetime) -> list[dict]:
    """The Pulse mirror itself, whatever the scheduler says: older than 26 h on a weekday is a problem."""
    try:
        sa = json.load(open(MIRROR_META)).get("synced_at")
        hours = (now.astimezone(dt.timezone.utc) - dt.datetime.fromisoformat(sa.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return [{"task": "pulse-mirror", "kind": "MIRROR_OLD", "hours": None}] if now.weekday() < 5 else []
    return [{"task": "pulse-mirror", "kind": "MIRROR_OLD", "hours": round(hours, 1)}] if hours > MIRROR_MAX_HOURS and now.weekday() < 5 else []


ALERT_KINDS = ("MISSED", "STALLED", "NEVER_RUN", "PULL_OLD", "MIRROR_OLD")


def log_problems(problems: list[dict], now: dt.datetime) -> None:
    """One event per problem; the key makes a repeat check write nothing."""
    if EV is None:
        return
    for p in problems:
        level = "alert" if p["kind"] in ALERT_KINDS else "warn"
        when = p.get("slot") or p.get("started") or p.get("session") or now.strftime("%Y-%m-%d")
        what = {"MISSED": "did not run its scheduled slot", "STALLED": "is stalled", "NEVER_RUN": "has never run", "LATE": "started late",
                "NOT_AUTO": "is not in auto permission mode", "PULL_OLD": "data is over 26 h old", "MIRROR_OLD": "is over 26 h old"}.get(p["kind"], p["kind"])
        text = f"{p['task']} {what}" + (f" (slot {p['slot']})" if p.get("slot") else "")
        EV.log_event("task_watch", p["kind"].lower(), level, text, key=f"task:{p['task']}:{p['kind']}:{when}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.now()
    problems = check(now) + pull_age(now) + mirror_age(now)
    log_problems(problems, now)
    hard = [p for p in problems if p["kind"] in ALERT_KINDS]
    if a.json:
        print(json.dumps({"as_of": now.isoformat(timespec="minutes"), "problems": problems}, indent=1)); return 1 if hard else 0
    print(f"{'RED' if hard else 'GREEN'}  scheduled tasks, as of {now:%Y-%m-%d %H:%M}")
    for p in problems:
        extra = {k: v for k, v in p.items() if k not in ("task", "kind")}
        print(f"  {'!' if p['kind'] in ALERT_KINDS else '~'} {p['task']}: {p['kind']} {extra}")
    if any(p["kind"] == "STALLED" for p in problems):
        print("  note: a STALLED run blocks that task's later slots. Open it in the app and answer its prompt, or stop it.")
    if a.notify and hard:
        sig = json.dumps(sorted((p["task"], p["kind"], p.get("slot", p.get("session", ""))) for p in hard))
        old = json.loads(STATE.read_text()).get("sig") if STATE.exists() else None
        if sig != old:                   # tell once per change, not every check
            names = ", ".join(sorted({f"{p['task']} {p['kind'].lower()}" for p in hard}))[:200]
            subprocess.run(["osascript", "-e", f'display notification "{names}" with title "Scheduled tasks need attention"'], check=False)
            STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps({"sig": sig, "at": now.isoformat(timespec="minutes")}))
    elif a.notify and not hard and STATE.exists():
        STATE.unlink()                   # healthy again: the next problem notifies afresh
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())

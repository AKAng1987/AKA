"""check_events.py -- checks for events.py (temp files only; never touches the real log)."""
import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events as E  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


d = tempfile.mkdtemp()
f = os.path.join(d, "events.jsonl")
now = dt.datetime(2026, 11, 10, 1, 0, tzinfo=dt.timezone.utc)

check("first write", E.log_event("mirror", "stale", "alert", "Mirror is 30 h old", key="k1", ts=now, file=f), True)
check("same key not written twice", E.log_event("mirror", "stale", "alert", "Mirror is 30 h old", key="k1", ts=now, file=f), False)
check("default key from source/kind/text dedupes", (E.log_event("a", "b", "info", "hello", file=f), E.log_event("a", "b", "info", "hello", file=f)), (True, False))
check("different key writes", E.log_event("mirror", "stale", "alert", "Mirror is 30 h old", key="k2", ts=now, file=f), True)
lines = [json.loads(x) for x in open(f)]
check("one JSON object per line with the 7 fields", sorted(lines[0]), sorted(["ts", "source", "kind", "level", "symbol", "text", "key"]))
check("ts is UTC Z", lines[0]["ts"], "2026-11-10T01:00:00Z")

E.log_event("x", "y", "warn", "account 123456789 balance " + "z" * 300, key="k3", file=f)
last = json.loads(open(f).read().splitlines()[-1])
check("long digit runs scrubbed, text capped", ("123456789" in last["text"], len(last["text"]) <= 200), (False, True))
check("bad level becomes info", (E.log_event("x", "y", "loud", "t", key="k4", file=f), json.loads(open(f).read().splitlines()[-1])["level"]), (True, "info"))
check("never raises on an unwritable path", E.log_event("x", "y", "info", "t", file="/proc/nope/events.jsonl"), False)

# reading and screen order
g = os.path.join(d, "e2.jsonl")
T = lambda h: now - dt.timedelta(hours=h)  # noqa: E731
E.log_event("calendar", "release", "warn", "CPI today", key="a", ts=T(3), file=g)
E.log_event("calendar", "risk", "alert", "PH over cap", key="b", ts=T(2), file=g)
E.log_event("mirror", "stale", "alert", "mirror old", key="c", ts=T(5), file=g)
E.log_event("cgi", "fresh", "warn", "1 problem", key="d", ts=T(4), file=g)
E.log_event("ph_daily", "build", "info", "ok", key="e", ts=T(1), file=g)
E.log_event("calendar", "release", "warn", "old news", key="f", ts=T(30), file=g)
rec = E.read_events(now - dt.timedelta(hours=24), file=g)
check("24h window drops the 30h-old event", len(rec), 5)
order = [e["text"] for e in E.screen_order(rec)]
check("alerts first, data health first within a level, info dropped", order, ["mirror old", "PH over cap", "1 problem", "CPI today"])
E.log_event("mirror", "synced", "info", "Pulse mirror synced", key="s1", ts=T(0), file=g)
check("a later mirror sync clears the stale-mirror alert, other alerts stay",
      [e["text"] for e in E.screen_order(E.read_events(now - dt.timedelta(hours=24), file=g))], ["PH over cap", "1 problem", "CPI today"])
check("missing file reads as empty", E.read_events(now, file=os.path.join(d, "none")), [])

# rotation
h = os.path.join(d, "e3.jsonl")
E.log_event("a", "b", "info", "ancient", key="o1", ts=now - dt.timedelta(days=200), file=h)
E.log_event("a", "b", "info", "recent", key="o2", ts=now - dt.timedelta(days=5), file=h)
check("rotate moves only old lines", E.rotate(file=h, now=now), 1)
check("log keeps the recent line", [json.loads(x)["text"] for x in open(h)], ["recent"])
arch = os.path.join(d, "events_archive", "events-2026-04.jsonl")
check("old line archived by month", os.path.exists(arch) and "ancient" in open(arch).read(), True)

# task_watch hooks
import task_watch as TW  # noqa: E402

tf = os.path.join(d, "tw.jsonl")
os.environ["EVENTS_FILE"] = tf
probs = [{"task": "pulse-mirror-sync", "kind": "MISSED", "slot": "2026-11-10T07:24"}, {"task": "x", "kind": "LATE", "slot": "2026-11-10T07:42", "started": "t"}]
TW.log_problems(probs, dt.datetime(2026, 11, 10, 12, 0))
TW.log_problems(probs, dt.datetime(2026, 11, 10, 19, 0))
rows = [json.loads(x) for x in open(tf)]
check("task_watch: one event per problem even when checked twice", len(rows), 2)
check("MISSED is an alert, LATE a warn", [(r["kind"], r["level"]) for r in rows], [("missed", "alert"), ("late", "warn")])
check("SLOTS lists the Pulse mirror sync", TW.SLOTS["pulse-mirror-sync"][1], "07:24")
os.environ.pop("EVENTS_FILE")

print(f"FAILS: {fails}")
sys.exit(1 if fails else 0)

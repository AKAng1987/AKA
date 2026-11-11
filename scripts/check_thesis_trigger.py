"""check_thesis_trigger.py -- checks for thesis_trigger.py. Temp dirs only; the real queue and mirror are never touched."""
import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thesis_trigger as T  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


today = dt.date(2026, 11, 10)
long_t = " ".join(["word"] * 200)
J = [
    {"id": "a", "type": "day", "ts": "2026-11-09T01:00:00Z", "text": long_t},
    {"id": "b", "type": "day", "ts": "2026-11-09T02:00:00Z", "text": " ".join(["w"] * 100)},          # short: no run
    {"id": "c", "type": "entry", "ts": "2026-11-09T03:00:00Z", "text": long_t},                          # wrong type
    {"id": "d", "type": "week", "ts": "2026-11-02T03:00:00Z", "text": long_t},                           # filed too long ago
    {"id": "e", "type": "week", "ts": "2026-11-10T03:00:00Z", "body": "<div>" + long_t + "</div>", "entry_date": "2026-11-08"},
    {"id": "f", "type": "day", "ts": "2026-11-09T04:00:00Z", "text": " ".join(["w"] * 150)},          # exactly 150: not over
]
check("only new, long, day/week entries queue", [e["id"] for e in T.new_theses(J, set(), today)], ["a", "e"])
check("an already queued entry is not queued again", [e["id"] for e in T.new_theses(J, {"a"}, today)], ["e"])
check("body-only entries are counted by their words", T.words(J[4]), 200)

d = tempfile.mkdtemp()
m = os.path.join(d, "mirror", "journal")
os.makedirs(m)
for j in J:
    json.dump(j, open(os.path.join(m, j["id"] + ".json"), "w"))
os.environ["EVENTS_FILE"] = os.path.join(d, "events.jsonl")
out = os.path.join(d, "out")
r1 = T.run(os.path.join(d, "mirror"), out, today)
r2 = T.run(os.path.join(d, "mirror"), out, today)
check("first run queues two, second run queues none", (len(r1), len(r2)), (2, 0))
q = json.load(open(os.path.join(out, "factcheck_queue.json")))
check("queue holds ids and dates only, never his text", sorted(set(k for e in q for k in e)), ["entry_date", "id", "type", "words"])
ev = [json.loads(x) for x in open(os.environ["EVENTS_FILE"])]
check("one event per entry, once", (len(ev), {e["kind"] for e in ev}), (2, {"thesis_new"}))
check("a short entry day: no run at all", T.run(os.path.join(d, "mirror"), os.path.join(d, "out2"), dt.date(2026, 12, 25)), [])
os.environ.pop("EVENTS_FILE")
print("FAILS:", fails)
sys.exit(1 if fails else 0)

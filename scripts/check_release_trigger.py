"""check_release_trigger.py -- checks for release_trigger.py. Fixtures only."""
import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_trigger as R  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


fresh = {"release_calendar": {"next": {"CPI": "2026-11-10", "GDP": "2026-11-25", "FOMC": "2026-12-09", "SLOOS": "2027-02-01"}}}
ph = {"header": {"bsp": {"next": "2026-12-17"}}, "calendar": {"events": []}}
us_live = [{"symbol": "MORT", "book": "US", "_state": "planned"}]
ph_live = [{"symbol": "ALI", "book": "PH", "_state": "open"}]
d10, d11 = dt.date(2026, 11, 10), dt.date(2026, 11, 11)

ev, pend = R.triggers(d10, us_live, fresh, ph, [])
check("CPI day with a US plan fires one before-read", [(e["kind"], e["key"]) for e in ev], [("release_before", "release_before:US:CPI (can flip inflation):2026-11-10")])
check("the event carries the release only, no plan wording", "MORT" in json.dumps(ev), False)
ev2, pend2 = R.triggers(d10, us_live, fresh, ph, pend)
check("a second run the same day adds nothing", ev2, [])
ev3, pend3 = R.triggers(d11, us_live, fresh, ph, pend2)
check("the next morning fires the after-read once and clears it", ([e["kind"] for e in ev3], pend3), (["release_after"], []))
check("release day with no position in that book: no run", R.triggers(d10, ph_live, fresh, ph, [])[0], [])
check("no live plans at all: no run", R.triggers(d10, [], fresh, ph, [])[0], [])
old = [{"book": "US", "label": "CPI (can flip inflation)", "date": "2026-10-01"}]
check("an old pending item is dropped without a read", R.triggers(d11, us_live, fresh, ph, old)[0], [])
bsp = {"header": {"bsp": {"next": "2026-12-17"}}, "calendar": {"events": []}}
check("BSP day with a PH position fires for PH", [e["key"] for e in R.triggers(dt.date(2026, 12, 17), ph_live, fresh, bsp, [])[0]], ["release_before:PH:BSP decision:2026-12-17"])

d = tempfile.mkdtemp()
sp = os.path.join(d, "s.json")
R.run(d10, us_live, fresh, ph, sp)
check("state is saved between runs", json.load(open(sp))["pending"][0]["label"], "CPI (can flip inflation)")
print("FAILS:", fails)
sys.exit(1 if fails else 0)

"""check_cgi_repair.py -- classification, attempt cap, pause lever and the no-write rule for cgi_repair.py. No network, no AWS."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cgi_repair as R  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


c = lambda t, n=0: R.classify(t, n)  # noqa: E731
check("COT late is wait", c("COT stalled: newest CFTC report is as of 2026-11-03")[0], "wait")
check("site unreachable wakes the API", c("site unreachable: timed out"), ("fix", "wake"))
check("brief timeout wakes the API", c("could not read brief: The read operation timed out"), ("fix", "wake"))
check("TAPE stuck runs the derived chain", c("TAPE SPY stuck at 2026-11-12"), ("fix", "derived_chain"))
check("pipeline lag runs the derived chain", c("pipeline workbook_behind SPY: x"), ("fix", "derived_chain"))
check("yahoo stall: first try is a re-run", c("stalled USDEUR (yahoo_finance): newest 2026-11-12, 1 behind"), ("fix", "yahoo_rerun"))
check("yahoo stall still there after a re-run is wait", c("stalled USDEUR (yahoo_finance): newest 2026-11-12", 1), ("wait", "provider_late"))
check("other source stall goes to Claude", c("stalled XYZ (marketstack): newest 2026-11-12")[0], "claude")
check("referee mismatch is never auto-fixed", c("referee close_mismatch SPX: 1% off")[0], "claude")
check("referee with no bars goes to Claude", c("referee received no TradingView bars from the last refresh"), ("claude", "refresh_routine"))
check("routine not run goes to Claude", c("refresh routine has not run in 60h+"), ("claude", "refresh_routine"))
check("calendar dates missing is a code fault", c("release calendar coverage: no CPI date in 75 days")[0], "code")
check("PH problem re-runs the PH job", c("ph prices: stale")[1], "ph_daily")
check("unknown goes to Claude", c("something new")[0], "claude")

# attempt cap
k = R.fault_key("TAPE SPY stuck at 2026-11-12")
check("fault key drops dates", k, "tape spy stuck at #")
p = R.plan(["TAPE SPY stuck at 2026-11-12"], {k: {"attempts": 2}})
check("after 2 tries a fix becomes claude", (p[0]["class"], p[0]["play"].endswith("_gave_up")), ("claude", True))
p = R.plan(["TAPE SPY stuck at 2026-11-12"], {k: {"attempts": 1}})
check("1 try is still fix", p[0]["class"], "fix")

# pause lever: main exits before health() when PAUSE exists
import tempfile  # noqa: E402
d = tempfile.mkdtemp()
R.PAUSE = os.path.join(d, "PAUSE")
open(R.PAUSE, "w").write("2026-11-16")
R.health = lambda: (_ for _ in ()).throw(AssertionError("health ran while paused"))
sys.argv = ["cgi_repair.py"]
check("pause lever exits first", R.main(), 0)

# no playbook action may write prices or Pulse: only these tools are named in the source
src = inspect.getsource(R.run_play)
bad = [w for w in ("put-item", "batch-write", "delete-item", "dynamodb", "pulse", "orders", "git push") if w in src.lower()]
check("run_play names no write tool", bad, [])

print("PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)

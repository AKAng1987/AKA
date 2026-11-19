"""check_earnings_calendar.py -- pure parts of earnings_calendar.py. No network, no AWS."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import earnings_calendar as E  # noqa: E402

fails = 0
def check(name, got, want):
    global fails
    ok = got == want; fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))

check("12 is confirmed", E.kind_of(12), "confirmed")
check("10 and 11 are estimates", (E.kind_of(10), E.kind_of(11)), ("est.", "est."))
check("time codes", (E.time_of(-1), E.time_of(1), E.time_of(0)), ("before open", "after close", None))
check("after-close stamp keeps its New York date", E.et_date(1794950460), "2026-11-17")   # 2026-11-17 21:21 UTC
check("missing stamp", E.et_date(None), None)
rows = [["X", "OTC", "stock", 9e12], ["X", "NYSE", "stock", 5], ["X", "NASDAQ", "stock", 50], ["X", "NASDAQ", "fund", 99]]
check("listing: US stock with the largest cap", E.pick_listing(rows)[1:4], ["NASDAQ", "stock", 50])
check("no US listing", E.pick_listing([["X", "LSE", "stock", 1]]), None)

def row(nxt, kind, last, held=None):
    return {"next": nxt, "next_kind": kind, "last": last, "held": held}
# first run
rs = {"A": row("2026-12-02", "est.", "2026-08-26")}
sc, ch = E.diff_snapshots(None, rs, "2026-11-19", [], [])
check("first run holds the date, logs nothing", (rs["A"]["held"]["date"], sc, ch), ("2026-12-02", [], []))
# date moves and firms up
prev = {"asof": "2026-11-19T00:00:00", "rows": {"A": dict(rs["A"])}}
rs2 = {"A": row("2026-12-03", "confirmed", "2026-08-26")}
sc, ch = E.diff_snapshots(prev, rs2, "2026-11-20", [], [])
check("date change is logged", [(c["from"], c["to"], c["to_kind"]) for c in ch], [("2026-12-02", "2026-12-03", "confirmed")])
check("new held date", rs2["A"]["held"]["date"], "2026-12-03")
# the company reports: scored against the held date
prev = {"asof": "2026-12-03T00:00:00", "rows": {"A": dict(rs2["A"])}}
rs3 = {"A": row("2027-03-04", "est.", "2026-12-03")}
sc, ch = E.diff_snapshots(prev, rs3, "2026-12-04", [], [])
check("scored: held 12-03 confirmed, actual 12-03, miss 0", [(s["held_date"], s["kind"], s["actual"], s["miss_days"]) for s in sc], [("2026-12-03", "confirmed", "2026-12-03", 0)])
check("next report now held", rs3["A"]["held"]["date"], "2027-03-04")
# an estimate that was wrong
prev = {"asof": "2026-11-01T00:00:00", "rows": {"B": {"next": "2026-11-10", "next_kind": "est.", "last": "2026-08-01", "held": {"date": "2026-11-10", "kind": "est.", "first_seen": "2026-11-01"}}}}
rs4 = {"B": row("2027-02-10", "est.", "2026-11-13")}
sc, _ = E.diff_snapshots(prev, rs4, "2026-11-14", [], [])
check("late report scores +3 days", sc[0]["miss_days"], 3)
print("PASS" if not fails else f"{fails} FAILED"); sys.exit(1 if fails else 0)

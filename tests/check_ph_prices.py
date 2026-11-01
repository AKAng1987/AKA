"""Checks for ph_prices_build: calendar returns, EMA, breadth arithmetic. Run: ~/cgi-vercel/api/.venv/bin/python tests/check_ph_prices.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
import ph_prices_build as P  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  got={got!r} want={want!r}"))


check("months_back clamps the day", P.months_back(dt.date(2026, 3, 31), 1), dt.date(2026, 2, 28))
check("months_back across a year", P.months_back(dt.date(2026, 1, 15), 3), dt.date(2025, 10, 15))
r = P.calendar_returns(["2025-12-31", "2026-09-30", "2026-10-23", "2026-10-30"], [50, 100, 120, 132])
check("1D return is against the previous session", r["d"], 10.0)
check("calendar returns: 1W, 1M, YTD from the prior year's last close", (r["w"], r["m"], r["y"]), (10.0, 32.0, 164.0))
check("ema flat series", round(P.ema([10.0] * 60, 50), 6), 10.0)

# breadth: 12 names, 300 sessions; every name rises one tick a day, except one that falls
def bars(sym, drift):
    out = []
    d = dt.date(2025, 1, 1)
    px = 100.0
    while len(out) < 300:
        if d.weekday() < 5:
            px += drift
            out.append({"date": d.isoformat(), "open": px, "high": px, "low": px, "close": px, "value": 1e7})
        d += dt.timedelta(days=1)
    return out


allb = {f"S{i}": bars(f"S{i}", 0.1) for i in range(11)}
allb["FALL"] = bars("FALL", -0.1)
b = P.breadth_blob(allb, list(allb), "2026-11-01T00:00:00+00:00")
g = {x["symbol"]: x["value"] for x in b["gauges"]}
check("breadth: 11 of 12 above every average", g, {"PHFD": 91.7, "PHTW": 91.7, "PHFI": 91.7, "PHTH": 91.7})
check("breadth: net new highs = 11 highs - 1 low", b["series"][-1]["net"], 10)
check("breadth: three days of positive net is green", (b["colour"], b["streak_days"] >= 3), ("green", True))
check("breadth: net highs start after 251 sessions of history", len(b["series"]), 49)
check("breadth needs enough names", P.breadth_blob({"S0": allb["S0"]}, ["S0"], "x") is None or True, True)
# an open session is not a close
M = dt.timezone(dt.timedelta(hours=8))
bars2 = [{"date": "2026-11-02", "close": 1}, {"date": "2026-11-03", "close": 2}]
check("run at 10:00 Tuesday drops today's bar", len(P.drop_open_session(bars2, dt.datetime(2026, 11, 3, 10, 0, tzinfo=M))), 1)
check("run at 15:10 Tuesday keeps it", len(P.drop_open_session(bars2, dt.datetime(2026, 11, 3, 15, 10, tzinfo=M))), 2)
check("run on Saturday keeps it (no session)", len(P.drop_open_session([{"date": "2026-10-30", "close": 1}], dt.datetime(2026, 10, 31, 9, 0, tzinfo=M))), 1)
# ── ph_run_check
import ph_run_check as R  # noqa: E402

M = dt.timezone(dt.timedelta(hours=8))
tue_1520 = dt.datetime(2026, 11, 3, 15, 20, tzinfo=M)
iso = lambda mins: (tue_1520.astimezone(dt.timezone.utc) - dt.timedelta(minutes=mins)).isoformat()
good = dict(screener={"asof": "2026-11-03", "built_at": iso(5)}, edge={"built_at": iso(10), "last_run": {"filings_tried": 10, "n_failed": 1}}, index={"scanned_at": iso(8)},
            bars={"bars": {"AAA": [["2026-11-02", 1, 1, 1, 1, 1], ["2026-11-03", 1, 1, 1, 1, 1]]}}, breadth={"as_of": "2026-11-03"})
check("expected date: Tuesday 15:20 is today; Tuesday 10:00 is Monday; Monday 10:00 is Friday; Saturday is Friday",
      [R.expected_asof(dt.datetime(2026, 11, 3, 15, 20, tzinfo=M)), R.expected_asof(dt.datetime(2026, 11, 3, 10, 0, tzinfo=M)), R.expected_asof(dt.datetime(2026, 11, 2, 10, 0, tzinfo=M)), R.expected_asof(dt.datetime(2026, 11, 7, 9, 0, tzinfo=M))],
      ["2026-11-03", "2026-11-02", "2026-10-30", "2026-11-06"])
check("run check: a good daily run is OK", R.assess(tue_1520, "daily", **good)["ok"], True)
stale = dict(good, screener={"asof": "2026-11-02", "built_at": iso(5)}, bars={"bars": {"AAA": [["2026-11-02", 1, 1, 1, 1, 1]], "BBB": [["2026-11-02", 1, 1, 1, 1, 1]]}}, breadth={"as_of": "2026-11-02"})
rs = R.assess(tue_1520, "daily", **stale)
check("run check: today's bar missing is a problem that says holiday or lag with counts", (rs["ok"], "0 of 2 names" in rs["messages"][0], "holiday" in rs["messages"][0]), (False, True, True))
check("run check: snapshots not stored by this run", [m for m in R.assess(tue_1520, "daily", **dict(good, edge={"built_at": iso(600)}))["messages"]], ["EDGE snapshot was not stored by this run"])
check("run check: many filings failing to parse", R.assess(tue_1520, "daily", **dict(good, edge={"built_at": iso(3), "last_run": {"filings_tried": 10, "n_failed": 6}}))["messages"][0][:2], "6 ")
check("run check: the evening edge-only run does not need prices", R.assess(dt.datetime(2026, 11, 3, 18, 40, tzinfo=M), "edge", screener=None, edge={"built_at": (dt.datetime(2026, 11, 3, 18, 40, tzinfo=M).astimezone(dt.timezone.utc) - dt.timedelta(minutes=4)).isoformat(), "last_run": {}}, index={"scanned_at": (dt.datetime(2026, 11, 3, 18, 40, tzinfo=M).astimezone(dt.timezone.utc) - dt.timedelta(minutes=3)).isoformat()}, bars=None, breadth=None)["ok"], True)
print("FAILS:", fails)
sys.exit(1 if fails else 0)

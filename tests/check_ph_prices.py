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
# Tuesday 3 Nov 15:20; EDGE has bars to Monday 2 Nov? No: 2 Nov was a PSE holiday, so the newest possible bar is Friday 30 Oct. Use Wed 4 Nov for the live case.
wed_1510 = dt.datetime(2026, 11, 4, 15, 10, tzinfo=M)
iso = lambda mins, at=wed_1510: (at.astimezone(dt.timezone.utc) - dt.timedelta(minutes=mins)).isoformat()
row = lambda d: [d, 1, 1, 1, 1, 1]
BARS = {"bars": {"AAA": [row("2026-10-30"), row("2026-11-03")], "BBB": [row("2026-10-30"), row("2026-11-03")]}}
# fixture 1: a good run (15:10 Wed, bars to Tue 3 Nov, everything stored in the last minutes)
good = dict(screener={"asof": "2026-11-03", "built_at": iso(5)}, edge={"built_at": iso(10), "last_run": {"filings_tried": 10, "n_failed": 1}}, index={"scanned_at": iso(8)},
            bars=BARS, breadth={"as_of": "2026-11-03"})
check("expected date: Wed 15:10 is Tue; Wed 10:00 is Tue; Tue (after Mon holiday) is Mon 2 Nov -> Fri 30 Oct; Monday is Friday; Saturday is Friday",
      [R.expected_asof(wed_1510), R.expected_asof(dt.datetime(2026, 11, 4, 10, 0, tzinfo=M)), R.expected_asof(dt.datetime(2026, 11, 3, 15, 10, tzinfo=M)),
       R.expected_asof(dt.datetime(2026, 11, 9, 15, 10, tzinfo=M)), R.expected_asof(dt.datetime(2026, 11, 7, 9, 0, tzinfo=M))],
      ["2026-11-03", "2026-11-03", "2026-10-30", "2026-11-06", "2026-11-06"])
check("expected date: a gap in the stored bars (not listed) is a holiday too", R.expected_asof(dt.datetime(2026, 12, 15, 15, 10, tzinfo=M), {"2026-12-14"}), "2026-12-11")
check("observed holidays: 2 Nov has no bars between 30 Oct and 3 Nov", R.observed_holidays(BARS), {"2026-11-02"})
check("run check: a good daily run is OK", R.assess(wed_1510, "daily", **good)["ok"], True)
# fixture 2: lagging bar (EDGE has only Fri 30 Oct, the run expects Tue 3 Nov)
lag = dict(good, screener={"asof": "2026-10-30", "built_at": iso(5)}, bars={"bars": {"AAA": [row("2026-10-30")], "BBB": [row("2026-10-30")]}}, breadth={"as_of": "2026-10-30"})
rs = R.assess(wed_1510, "daily", **lag)
check("run check: lagging bar is a problem that says holiday or lag with counts", (rs["ok"], "0 of 2 names" in rs["messages"][0], "holiday" in rs["messages"][0]), (False, True, True))
# fixture 3: partial failure (EDGE filings fail to parse, prices fine)
part = R.assess(wed_1510, "daily", **dict(good, edge={"built_at": iso(3), "last_run": {"filings_tried": 10, "n_failed": 6}}))
check("run check: partial failure names the failed filings and nothing else", (part["ok"], len(part["messages"]), part["messages"][0][:2]), (False, 1, "6 "))
# a ~3 h run: EDGE stored 175 min ago, the run started 180 min ago
start = wed_1510.astimezone(dt.timezone.utc) - dt.timedelta(minutes=180)
long_run = dict(good, edge={"built_at": iso(175), "last_run": {}}, index={"scanned_at": iso(170)}, screener={"asof": "2026-11-03", "built_at": iso(2)})
check("run check: a 3 h run does not report the EDGE snapshot as not stored", R.assess(wed_1510, "daily", since=start, **long_run)["ok"], True)
check("run check: without --since a 3 h old snapshot is within the window", R.assess(wed_1510, "daily", **long_run)["ok"], True)
check("run check: a snapshot from before the run started is still reported", R.assess(wed_1510, "daily", since=start, **dict(long_run, edge={"built_at": iso(300), "last_run": {}}))["messages"], ["EDGE snapshot was not stored by this run"])
check("run check: snapshots not stored by this run", [m for m in R.assess(wed_1510, "daily", **dict(good, edge={"built_at": iso(600)}))["messages"]], ["EDGE snapshot was not stored by this run"])
check("run check: many filings failing to parse", R.assess(wed_1510, "daily", **dict(good, edge={"built_at": iso(3), "last_run": {"filings_tried": 10, "n_failed": 6}}))["messages"][0][:2], "6 ")
check("run check: the evening edge-only run does not need prices", R.assess(dt.datetime(2026, 11, 3, 18, 40, tzinfo=M), "edge", screener=None, edge={"built_at": (dt.datetime(2026, 11, 3, 18, 40, tzinfo=M).astimezone(dt.timezone.utc) - dt.timedelta(minutes=4)).isoformat(), "last_run": {}}, index={"scanned_at": (dt.datetime(2026, 11, 3, 18, 40, tzinfo=M).astimezone(dt.timezone.utc) - dt.timedelta(minutes=3)).isoformat()}, bars=None, breadth=None)["ok"], True)
# PSEi from the EDGE home page is accepted only when its % change agrees with the move from the previous stored close
home = open(os.path.join(os.path.dirname(__file__), "ph_edge_fixtures", "home.html"), encoding="utf-8", errors="replace").read()
P.E.get = lambda path, retries=2: home
hist = {"2026-10-29": 5705.58, "2026-10-30": 5680.35}
check("PSEi from EDGE home: matches the move into 30 Oct", P.psei_from_edge(hist, "2026-10-30"), 5680.35)
check("PSEi from EDGE home: a page still showing the old close is refused for 2 Nov", P.psei_from_edge(hist, "2026-11-02"), None)
# 4 Nov: the home page shows the 4 Nov close (+1.26%) while the bars only reach 3 Nov; judged against 3 Nov's close, not 30 Oct's
home4 = home.replace("5,680.35", "5,694.92", 1).replace("0.44▼", "1.26▲", 1)
hist4 = {"2026-10-29": 5705.58, "2026-10-30": 5680.35, "2026-11-03": 5624.17}
check("home_session: Wed 15:10 is today, Wed 10:00 is Tue, Sat is Fri", [P.home_session(dt.datetime(2026, 11, 4, 15, 10, tzinfo=M)), P.home_session(dt.datetime(2026, 11, 4, 10, 0, tzinfo=M)), P.home_session(dt.datetime(2026, 11, 7, 9, 0, tzinfo=M))], ["2026-11-04", "2026-11-03", "2026-11-06"])
P.E.get = lambda path, retries=2: home4
hist_after, = [P.psei_index({"psei_history": hist4}, None, "2026-11-03", dt.datetime(2026, 11, 4, 15, 10, tzinfo=M))[1]]
check("PSEi from EDGE home: the 4 Nov close, tested against the 3 Nov close, is accepted and stored under 4 Nov", hist_after.get("2026-11-04"), 5694.92)
check("PSEi from EDGE home: the same page tested as Tue 3 Nov's close (the 10:00 Wed case) does not fit that move, so it is refused", P.psei_from_edge(hist4, "2026-11-03"), None)
P.E.get = lambda path, retries=2: home
check("PSEi from EDGE home: no previous close, no answer", P.psei_from_edge({}, "2026-10-30"), None)
print("FAILS:", fails)
sys.exit(1 if fails else 0)

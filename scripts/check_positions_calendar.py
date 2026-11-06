"""Offline checks for scripts/positions_calendar.py (fixtures only; no network, nothing written to the mirror).
Run: python3 scripts/check_positions_calendar.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import positions_calendar as P  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok  " if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")


D = dt.date
today = D(2026, 11, 6)  # a Friday

# trading days
check("same day", P.trading_days_away(today, today), 0)
check("Fri -> Mon is 1 trading day", P.trading_days_away(today, D(2026, 11, 9)), 1)
check("Fri -> Tue is 2", P.trading_days_away(today, D(2026, 11, 10)), 2)
check("Fri -> next Fri is 5", P.trading_days_away(today, D(2026, 11, 13)), 5)
check("yesterday is -1", P.trading_days_away(today, D(2026, 11, 5)), -1)

# live plans
plans = [
    {"id": "A", "symbol": "AAA", "book": "PH", "status": "planned"},
    {"id": "B", "symbol": "BBB", "book": "PH", "status": "planned"},        # filled and exited -> dropped
    {"id": "C", "symbol": "CCC", "book": "PH", "status": "not triggered"},
    {"id": "D", "symbol": "DDD", "book": "US", "status": "passed"},
    {"id": "E", "symbol": "EEE", "book": "PH", "status": "expired"},        # but open by a fill -> kept
    {"id": "F", "symbol": "FFF", "book": "US", "status": "planned"},
]
results = {"B": {"fill": 10, "exit": 12, "ts": "2026-11-01T00:00:00Z"}, "E": {"fill": 5, "exit": None, "ts": "2026-11-04T00:00:00Z"},
           "A": {"fill": 3, "exit": None, "ts": "2026-11-05T00:00:00Z"}}
lv = {p["symbol"]: p["_state"] for p in P.live_plans(plans, results)}
check("live set: planned, open; closed, not triggered, passed dropped", lv, {"AAA": "open", "EEE": "open", "FFF": "planned"})

# exit date from text
ed = lambda t: (P.exit_from_text(t, today) or [None])[0]  # noqa: E731
check("ISO date", ed("close it by 2026-11-20 whatever"), D(2026, 11, 20))
check("20 Nov", ed("sell before 20 Nov earnings"), D(2026, 11, 20))
check("Nov 20", ed("out Nov 20th"), D(2026, 11, 20))
check("past month-day rolls to next year", ed("by Jan 5"), D(2027, 1, 5))
check("a date 10 days past stays this year", ed("was 27 Oct"), D(2026, 10, 27))
check("no date -> None", P.exit_from_text("Oil breaks out, yields break out, or price.", today), None)
check("empty text -> None", P.exit_from_text("", today), None)

# macro rows
fresh = {"release_calendar": {"next": {"FOMC": "2026-12-09", "CPI": "2026-11-10", "GDP": "2026-11-25", "SLOOS": "2027-02-01"}}}
ph = {"header": {"bsp": {"next": "2026-12-17"}},
      "calendar": {"asof": "2026-11-01T14:39:01+00:00", "events": [
          {"date": "2026-11-05", "kind": "cpi", "period": "Oct"},
          {"date": "2026-11-09", "kind": "gdp", "period": "Q3", "title": "YoY"}, {"date": "2026-11-09", "kind": "gdp", "period": "Q3", "title": "QoQ"},
          {"date": "2026-11-12", "kind": "pmi"}]},
      "earnings": {"next": [{"s": "SMPH", "date": "2026-11-12", "basis": "filed 2025-11-12 last year"},
                            {"s": "ALI", "date": "2026-11-16", "basis": "filed 2025-11-14 last year"},
                            {"s": "FAR", "date": "2026-12-30", "basis": "x"}],
                   "reported": [{"s": "MBT", "kind": "Q", "period": "2026-09-30", "announced": "2026-11-05T09:06", "ni_yoy": -8}]},
      "material": [
          {"symbol": "ALI", "chip": "material", "announced": "2026-11-05T12:17", "subject": "ALI 9M 2026 Financial and Operating Results Press Release"},
          {"symbol": "ALI", "chip": "press", "announced": "2026-11-05T12:16", "title": "Press Release"},
          {"symbol": "SMPH", "chip": "buyback", "announced": "2026-10-30T09:00", "title": "Share Buy-Back Transactions"}],
      "as_of": "2026-11-06", "freshness": {"edge": {"age_days": 1}}}
us = P.macro_rows("US", today, fresh, ph)
check("US macro: next of each type, sorted", [(r["date"].isoformat(), r["label"].split()[0]) for r in us],
      [("2026-11-10", "CPI"), ("2026-11-25", "GDP"), ("2026-12-09", "FOMC"), ("2027-02-01", "SLOOS")])
check("US macro labels carry the axis", us[0]["label"], "CPI (can flip inflation)")
phm = P.macro_rows("PH", today, fresh, ph)
check("PH macro: GDP once (deduped), BSP, no past CPI, no PMI", [(r["date"].isoformat(), r["label"]) for r in phm],
      [("2026-11-09", "PH GDP Q3"), ("2026-12-17", "BSP decision")])

# company events
ev, out = P.company_events("ALI", today, ph)
check("ALI: results out, press release filed yesterday, 17-Q still expected 16 Nov",
      ([e["kind"] for e in ev], out, ev[-1]["date"].isoformat(), "press release already out" in ev[-1]["label"]), (["filed", "results"], True, "2026-11-16", True))
check("bare 'Press Release' is not listed", [e["label"] for e in ev if e["label"].endswith("Press Release") and "Financial" not in e["label"]], [])
ev, out = P.company_events("MBT", today, ph)
check("MBT: filed, results out, no expected date", ([e["kind"] for e in ev], out), (["filed"], True))
ev, out = P.company_events("SMPH", today, ph)
check("SMPH: old buy-back (6 days ago) not 'filed in the last 2 days'; expected 12 Nov listed", [(e["kind"], e["date"].isoformat()) for e in ev], [("results", "2026-11-12")])
ev, _ = P.company_events("FAR", today, ph)
check("expected date beyond 21 days is not listed", ev, [])

# build: header, stale mirror, nothing scheduled, US ETF, markers
meta = {"synced_at": "2026-11-06T00:50:13Z"}
now = dt.datetime(2026, 11, 6, 2, 0, tzinfo=dt.timezone.utc)
plans2 = [{"id": "A", "symbol": "SMPH", "book": "PH", "status": "planned", "plan_ts": "2026-11-05T00:00:00Z", "entry": 17.8, "stop": 16.8,
           "wrong": "exit 2026-11-09 at the latest"},
          {"id": "Z", "symbol": "ZZZ", "book": "PH", "status": "planned", "plan_ts": "2026-11-05T00:00:00Z", "entry": 1, "stop": 0.9},
          {"id": "M", "symbol": "MORT", "book": "US", "status": "planned", "plan_ts": "2026-11-05T00:00:00Z", "entry": 8, "stop": 7.5,
           "wrong": "Oil breaks out"}]
text, n_soon = P.build(today, plans2, {}, meta, fresh | {"as_of": "2026-11-06", "n_problems": 0}, ph, [], now)
check("PH 'nothing scheduled' printed for a name with no events", "ZZZ" in text and "nothing scheduled for this name in 21 days" in text, True)
check("own exit from text labelled", "exit, from your text" in text, True)
check("MORT shows CPI, GDP, FOMC and says earnings not checked", all(x in text for x in ("CPI (can flip inflation)", "GDP (can flip growth)", "FOMC (can flip liquidity)", "US earnings: not checked")), True)
check("exit date: not stated for a plan with no date", "exit date: not stated" in text, True)
check("'!' marks events within 3 trading days (PH GDP Mon, CPI Tue, own exit Mon)", n_soon >= 3, True)
check("no stale header on a fresh mirror", "STALE" in text, False)
old = dt.datetime(2026, 11, 8, 2, 0, tzinfo=dt.timezone.utc)
text2, _ = P.build(today, plans2, {}, meta, fresh, ph, [], old)
check("stale mirror said in the header", "PULSE MIRROR STALE" in text2.split("\n\n")[0], True)
ph_old = dict(ph, calendar=dict(ph["calendar"], asof="2026-10-20T00:00:00+00:00"))
text3, _ = P.build(today, plans2, {}, meta, fresh, ph_old, [], now)
check("stale PH calendar said in the header", "PH macro calendar last refreshed 2026-10-20" in text3, True)
text4, _ = P.build(today, [], {}, meta, None, None, ["could not read /api/ph: boom"], now)
check("unreadable sources reported, empty books say so", ("unreadable" in text4, "PH: no live plans" in text4, "US: no live plans" in text4, "boom" in text4), (True, True, True, True))

print("FAILS:", fails)
sys.exit(1 if fails else 0)

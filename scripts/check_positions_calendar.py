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
check("text dates are no longer guessed as exits", "from your text" in text, False)
check("wrong line printed word for word", "wrong: Oil breaks out" in text, True)
check("MORT shows CPI, GDP, FOMC and says earnings not checked", all(x in text for x in ("CPI (can flip inflation)", "GDP (can flip growth)", "FOMC (can flip liquidity)", "US earnings: not checked")), True)
check("exit date: not stated for a plan with no date", "exit date: not stated" in text, True)
check("'!' marks events within 3 trading days (PH GDP Mon, CPI Tue)", n_soon >= 2, True)
check("no stale header on a fresh mirror", "STALE" in text, False)
old = dt.datetime(2026, 11, 8, 2, 0, tzinfo=dt.timezone.utc)
text2, _ = P.build(today, plans2, {}, meta, fresh, ph, [], old)
check("stale mirror said in the header", "PULSE MIRROR STALE" in text2.split("\n\n")[0], True)
ph_old = dict(ph, calendar=dict(ph["calendar"], asof="2026-10-20T00:00:00+00:00"))
text3, _ = P.build(today, plans2, {}, meta, fresh, ph_old, [], now)
check("stale PH calendar said in the header", "PH macro calendar last refreshed 2026-10-20" in text3, True)
text4, _ = P.build(today, [], {}, meta, None, None, ["could not read /api/ph: boom"], now)
check("unreadable sources reported, empty books say so", ("unreadable" in text4, "PH: no live plans" in text4, "US: no live plans" in text4, "boom" in text4), (True, True, True, True))

# exit_by on the plan (handoff 02)
pe = P.plan_exit({"exit_by": "2026-11-12", "exit_reason": " before FOMC ", "wrong": "out by 20 Nov"}, today)
check("exit_by with trimmed reason; text ignored", (pe[0].isoformat(), pe[1]), ("2026-11-12", "before FOMC"))
check("no exit_by -> None even with a date in the text", P.plan_exit({"wrong": "out by 20 Nov"}, today), None)
check("bad exit_by ignored", P.plan_exit({"exit_by": "soon"}, today), None)
base = {"book": "PH", "status": "planned", "plan_ts": "2026-11-05T00:00:00Z", "entry": 1, "stop": .9}
t5, n5 = P.build(today, [dict(base, id="X", symbol="XXX", exit_by="2026-11-09", exit_reason="before FOMC Dec 9")], {}, meta, fresh, ph, [], now)
check("exit_by in window: '!' and reason", ("exit by: before FOMC Dec 9" in t5, "! 2026-11-09" in t5), (True, True))
t6, _ = P.build(today, [dict(base, id="X", symbol="XXX", exit_by="2027-03-01")], {}, meta, fresh, ph, [], now)
check("exit_by outside the window said so with the date", "exit by 2027-03-01" in t6, True)
t7, n7 = P.build(today, [dict(base, id="X", symbol="XXX", exit_by="2026-11-05", exit_reason="test")], {}, meta, fresh, ph, [], now)
check("past exit date leads the screen", t7.startswith("!! PAST EXIT DATE: XXX (planned) exit by 2026-11-05, 1d ago (test)"), True)
check("past exit counts as urgent", n7 >= 1, True)
check("no past line when exit is today or later", "PAST EXIT" in t5, False)

# ── open risk (handoff 01) ───────────────────────────────────────────────────────────────────────
def lp(sym, book, entry, stop, shares, fill=None, **kw):
    pl = {"id": sym, "symbol": sym, "book": book, "status": "planned", "plan_ts": "2026-11-05T00:00:00Z", "entry": entry, "stop": stop, "shares": shares}
    pl.update(kw)
    res = {sym: {"fill": fill, "exit": None, "ts": "2026-11-05T00:00:00Z"}} if fill is not None else {}
    return pl, res

def live_of(*pairs):
    pl = [a for a, _ in pairs]
    rs = {}
    for _, r in pairs:
        rs.update(r)
    return P.live_plans(pl, rs)

r1 = P.plan_risk(live_of(lp("A", "PH", 15.5, 15, 50000, fill=15.55))[0])
check("open uses the fill, not the entry: 27,500 / 25,000 = 1.10R", round(r1["r"], 2), 1.10)
r2 = P.plan_risk(live_of(lp("A", "PH", 15.5, 15, 50000))[0])
check("planned uses the entry: 25,000 = 1.00R", round(r2["r"], 2), 1.00)
r3 = P.plan_risk(live_of(lp("A", "PH", 15.5, 16, 50000, fill=15.55))[0])
check("stop above fill is 0R, never negative", (r3["r"], r3["locked"]), (0.0, True))
for name, kw in (("no stop", {"stop": None}), ("zero shares", {"shares": 0}), ("text shares", {"shares": "abc"})):
    pl, rs = lp("A", "PH", 15.5, 15, 50000, fill=15.55)
    pl.update(kw)
    check(f"{name}: risk unknown", P.plan_risk(P.live_plans([pl], rs)[0])["unknown"], True)
pl, rs = lp("A", "PH", 15.5, 15, 50000, fill=15.55)
lv_ = P.live_plans([pl], rs)
lv_[0]["_fill"] = None
check("open trade with no fill: risk unknown", P.plan_risk(lv_[0])["unknown"], True)

check("1.39% under", P.cap_status(1.39), "under")
check("1.40% at cap", P.cap_status(1.40), "AT CAP")
check("1.50% at cap", P.cap_status(1.50), "AT CAP")
check("1.51% OVER", P.cap_status(1.51), "OVER")
check("1.504% rounds to 1.50, at cap", P.cap_status(1.504), "AT CAP")

# book: 5 open + 1 planned (PH) and one planned US; closed and not-triggered ignored
ph_open = [lp("ALI", "PH", 15.5, 15, 50000, fill=15.55), lp("BLOOM", "PH", 1.92, 1.8, 208333, fill=1.91),
           lp("JFC", "PH", 145, 138, 3571, fill=147), lp("JGS", "PH", 17.5, 16.5, 25000, fill=17.5), lp("MBT", "PH", 62, 60, 12500, fill=62.3)]
smph = lp("SMPH", "PH", 17.8, 16.8, 25000)
mort = lp("MORT", "US", 8, 7.5, 1200)
wday = lp("WDAY", "US", 195.5, 184.5, 54, status="not triggered")
closed = lp("OLD", "PH", 10, 9, 1000, fill=10)
closed[1]["OLD"]["exit"] = 11
live = live_of(*ph_open, smph, mort, wday, closed)
rk = P.open_risk(live)
check("PH live 5.45R = 1.36%", (round(rk["PH"]["live_r"], 2), rk["PH"]["live_pct"]), (5.45, 1.36))
check("PH if all trigger 6.45R = 1.61% OVER", (round(rk["PH"]["all_r"], 2), rk["PH"]["all_pct"], rk["PH"]["status"]), (6.45, 1.61, "OVER"))
check("US live 0, if all trigger 1R = 0.25% under", (rk["US"]["live_pct"], rk["US"]["all_pct"], rk["US"]["status"]), (0.0, 0.25, "under"))
check("not triggered and closed plans left out", sorted(i["symbol"] for i in rk["US"]["items"] + rk["PH"]["items"]),
      sorted(["ALI", "BLOOM", "JFC", "JGS", "MBT", "SMPH", "MORT"]))
rl = P.risk_lines(live)
check("OVER line leads", rl[0].startswith("!! OPEN RISK OVER CAP: PH if all trigger 1.61%"), True)
check("combined sum, no cap", "combined 1.86% if all trigger (for information, no cap)" in " ".join(rl), True)
check("partials not seen printed", "partials not seen" in " ".join(rl), True)
rk0 = P.open_risk([])
check("empty book shows 0.00% under", (rk0["PH"]["all_pct"], rk0["PH"]["status"]), (0.0, "under"))
pl_u, rs_u = lp("Q", "PH", 15.5, 15, 50000)
pl_u["stop"] = None
rlu = P.risk_lines(P.live_plans([pl_u], rs_u))
check("unknown risk: INCOMPLETE and 'at least', never zero", ("INCOMPLETE (1 unknown)" in rlu[0], "at least" in rlu[0], "Q risk unknown" in " ".join(rlu)), (True, True, True))
tx, ns = P.build(today, [a for a, _ in ph_open] + [smph[0], mort[0]], {k: v for _, r in ph_open for k, v in r.items()}, meta,
                 fresh | {"as_of": "2026-11-06", "n_problems": 0}, ph, [], now)
check("build: first line is the OVER flag, RISK line under the header, per-plan risk shown",
      (tx.splitlines()[0].startswith("!! OPEN RISK OVER CAP"), "\nRISK  PH live" in tx, "| risk 1.10R" in tx), (True, True, True))

# ── event log (handoff 03) ───────────────────────────────────────────────────────────────────────
import json  # noqa: E402
import tempfile  # noqa: E402
import events as EV  # noqa: E402

old_now = dt.datetime(2026, 11, 8, 2, 0, tzinfo=dt.timezone.utc)      # mirror (06 Nov 00:50Z) is ~49 h old
fresh_bad = fresh | {"as_of": "2026-11-06", "n_problems": 1, "price_jumps": {"n_problems": 1}}
base = {"book": "PH", "status": "planned", "plan_ts": "2026-11-05T00:00:00Z", "entry": 1, "stop": .9, "shares": 1000, "oneR": 100}
live_ev = P.live_plans([dict(base, id="X", symbol="XXX", exit_by="2026-11-05", exit_reason="test"),
                        dict(base, id="Y", symbol="YYY", book="US", exit_by="2026-11-09")], {})
ev = P.calendar_events(today, old_now, live_ev, meta, fresh_bad, ph, [])
kinds = {(e["source"], e["kind"], e["level"]) for e in ev}
check("stale mirror is an alert on a weekday", ("mirror", "stale", "alert") in kinds, True)
check("CGI freshness problem is a warn naming the section", any(e["source"] == "cgi" and "price_jumps" in e["text"] for e in ev), True)
check("past exit is an alert", ("calendar", "past_exit", "alert") in kinds, True)
check("exit within 3 trading days is a warn", ("calendar", "exit_soon", "warn") in kinds, True)
check("mirror synced is info", ("mirror", "synced", "info") in kinds, True)
check("persistent conditions carry the day in their key",
      all(e["key"].endswith(today.isoformat()) or e["kind"] == "synced" for e in ev), True)
sat = dt.date(2026, 11, 7)
ev_sat = P.calendar_events(sat, old_now, live_ev, meta, fresh, ph, [])
check("stale mirror not alerted on a weekend", ("mirror", "stale", "alert") in {(e["source"], e["kind"], e["level"]) for e in ev_sat}, False)
over = P.calendar_events(today, old_now, P.live_plans([dict(base, id="O%d" % i, symbol="S%d" % i, entry=11, stop=10, shares=25000, oneR=25000)
                                                       for i in range(7)], {}), meta, fresh, ph, [])
check("risk over cap is an alert", any(e["kind"] == "risk_over" and e["level"] == "alert" for e in over), True)
blob = json.dumps(ev + over).lower()
check("no account, balance or plan-wording text in events", any(w in blob for w in ("account", "balance", "wrong")), False)

# regime change
sp = os.path.join(tempfile.mkdtemp(), "state.json")
check("first regime run records a baseline, no event", P.regime_event("C1G2", sp, today), None)
check("same regime, no event", P.regime_event("C1G2", sp, today), None)
rg = P.regime_event("C2G2", sp, today)
check("regime change is an alert naming both", (rg["level"], rg["text"]), ("alert", "Regime changed: C1G2 to C2G2"))
check("no code, no event", P.regime_event(None, sp, today), None)

# screen block
scr = EV.screen_order([dict(e, ts="2026-11-08T01:00:00Z") for e in ev])
tb, _ = P.build(today, [dict(base, id="X", symbol="XXX")], {}, meta, fresh_bad, ph, [], old_now, events=scr)
check("SINCE YESTERDAY leads, stale mirror is its first line", (tb.startswith("SINCE YESTERDAY: "), tb.splitlines()[1].startswith("  !! PULSE MIRROR STALE")), (True, True))
check("old stale lines are folded into the block (said once)", (tb.count("PULSE MIRROR STALE"), "\n  ~ PULSE" in tb), (1, False))
tn, _ = P.build(today, [], {}, {"synced_at": "2026-11-06T00:50:13Z"}, fresh | {"as_of": "x", "n_problems": 0}, ph, [], now, events=[])
check("nothing to flag said plainly", tn.startswith("SINCE YESTERDAY: nothing to flag"), True)

ts_, _ = P.build(today, [], {}, {"synced_at": "2026-11-06T00:50:13Z"}, fresh | {"as_of": "x", "n_problems": 0}, ph, [], now, events=[],
                 stress=["STRESS PH  at stops 1.00%", "       cluster PH: 1 name(s)"])
check("stress lines are shown under the risk block", "STRESS PH  at stops 1.00%" in ts_, True)
ts0, _ = P.build(today, [], {}, {"synced_at": "2026-11-06T00:50:13Z"}, fresh | {"as_of": "x", "n_problems": 0}, ph, [], now, events=[])
check("no stress lines when none are given", "STRESS" in ts0, False)

print("FAILS:", fails)
sys.exit(1 if fails else 0)

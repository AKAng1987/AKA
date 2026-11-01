"""Parser checks on saved EDGE pages. Run: python3 tests/check_ph_edge.py"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import ph_edge as e  # noqa: E402

fx = lambda n: open(os.path.join(HERE, "ph_edge_fixtures", n), encoding="utf-8", errors="replace").read()
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  got={got!r} want={want!r}"))


# numbers
check("num comma", e.num("1,234.5"), 1234.5)
check("num paren negative", e.num("(12)"), -12.0)
check("num dash", e.num("-"), None)
check("money B", e.money("Php15.84B"), 15.84e9)
check("yoy", e.yoy(110, 100), 10.0)
check("yoy from loss is None", e.yoy(10, -5), None)

# annual report: ALI FY2025 (Php thousands)
a = e.parse_results(fx("ali_fy25_17a.html"))
check("17-A kind/period", (a["kind"], a["period"]), ("A", "2025-12-31"))
check("17-A revenue / prev", (a["revenue"], a["revenue_prev"]), (190210680.0, 180737527.0))
check("17-A net income parent", (a["net_income_parent"], a["net_income_parent_prev"]), (39120819.0, 28232553.0))
check("17-A net income after tax", a["net_income"], 45554129.0)
check("17-A eps basic", (a["eps"], a["eps_prev"]), (2.7, 1.9))
check("17-A scale from either wording", (a["scale"], e.parse_results(fx("ali_q2_17q.html"))["scale"]), (1000.0, 1000.0))

# quarterly report: ALI Q2 2026
q = e.parse_results(fx("ali_q2_17q.html"))
check("17-Q kind/period", (q["kind"], q["period"]), ("Q", "2026-06-30"))
check("17-Q revenue q/prev/ytd/prev ytd", (q["revenue"], q["revenue_prev"], q["revenue_ytd"], q["revenue_prev_ytd"]),
      (37493017.0, 39513459.0, 74977002.0, 83068970.0))
check("17-Q net income parent q/prev", (q["net_income_parent"], q["net_income_parent_prev"]), (6088685.0, 7226478.0))
check("17-Q eps q/ytd", (q["eps"], q["eps_ytd"]), (0.42, 0.8))
check("17-Q revenue yoy", e.yoy(q["revenue"], q["revenue_prev"]), -5.1)

# buy-back: ICT 28 Oct 2026
b = e.parse_buyback(fx("buyback_ict.html"))
check("buyback shares", b["shares"], 250000.0)
check("buyback cumulative (footnote 1 is not part of it)", b["cumulative"], 86029020.0)
check("buyback outstanding/treasury after", (b["outstanding_after"], b["treasury_after"]), (2017432695.0, 27744976.0))
check("buyback total repurchased", b["total_repurchased"], 15.84e9)
check("buyback dates", (b["report_date"], b["first"], b["last"]), ("2026-10-28", "2026-10-28", "2026-10-28"))
check("buyback avg price in range", 820 < b["avg_price"] < 840, True)
check("buyback n trades", b["n_trades"], 27)

# listings
l = e.parse_listing(fx("ann_buyback.html"))
check("listing first row", (l[0]["cmpy_id"], l[0]["company"], l[0]["form"], l[0]["announced"]), ("624", "Cebu Air, Inc.", "9-1", "2026-10-30T08:08"))
check("listing edge_no is the hash", l[0]["edge_no"], "79b14647592470bd64d70b69f0a3140b")
f = e.parse_listing(fx("fin_ali.html"))
check("amended filing detected", [(x["amend"], x["title"]) for x in f if x["amend"]][:1], [(1, "Quarterly Report")])
check("report number kept", f[0]["number"], "CR06100-2026")
w = e.parse_listing(fx("listing_week.html"))
check("week listing has rows", len(w) > 30, True)
check("pages marker", e.pages("<b>[1 /\r\n6]</b>"), 6)

# directory
d = e.parse_directory(fx("dir_p1.html"))
check("directory first", (d[0]["symbol"], d[0]["cmpy_id"], d[0]["sector"], d[0]["listed"]), ("AAA", "55", "Holding Firms", "1973-03-22"))
check("directory rows", len(d) > 40, True)
check("chips", (e.chip("9-1"), e.chip("13-1"), e.chip("4-30"), e.chip("6-1"), e.chip("ZZ-9")), ("buyback", "insider", "material", "dividend", "other"))
# chart bars and stock page (ALI, 30 Oct 2026)
import json  # noqa: E402

bars = e.parse_chart(json.load(open(os.path.join(HERE, "ph_edge_fixtures", "chart_ali.json"))))
check("chart: 9 sessions, oldest first", (len(bars), bars[0]["date"], bars[-1]["date"]), (9, "2026-10-20", "2026-10-30"))
check("chart: last close/value", (bars[-1]["close"], bars[-1]["value"]), (15.36, 27139292.0))
sp = e.parse_stock_page(fx("stock_ali.html"))
check("stock page: float, outstanding, last", (sp["free_float"], sp["outstanding"], sp["last"]), (44.84, 14292521069.0, 15.36))
check("stock page: 52-week range", (sp["hi52"], sp["lo52"]), (23.65, 12.58))
# detail forms
m = e.parse_detail(fx("mat.html"), "material")
check("material subject", m["subject"].startswith("Initial Assessment Report in connection with Alternergy"), True)
dv = e.parse_detail(fx("div.html"), "dividend")
check("dividend figures", (dv["dividend"]["per_share"], dv["dividend"]["record"], dv["dividend"]["payment"], dv["dividend"]["type"]), (39.741875, "2026-11-16", "2026-11-29", "Regular"))
check("dividend subject", "Preferred" in dv["subject"], True)
ins = e.parse_detail(fx("ins.html"), "insider")["insider"]
check("insider: who, side, net, value", (ins["name"], ins["side"], ins["net_shares"], ins["net_value"]), ("Virgilio B. Villar", "bought", 20000.0, 4800.0))
check("insider position", ins["position"], "Chairman of the Board")
print("FAILS:", fails)
sys.exit(1 if fails else 0)

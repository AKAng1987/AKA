"""check_filings_build.py -- the pure parsers in filings_build.py. Fixtures only: no network, no AWS, no user agent."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import filings_build as F  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


IDX = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------
4           ACME CORP                                                     1234        20261118    edgar/data/1234/0001234567-26-000011.txt
4           DOE JANE                                                      99887       20261118    edgar/data/1234/0001234567-26-000011.txt
SCHEDULE 13D/A  BIG HOLDER LP                                             555         20261118    edgar/data/777/0000555555-26-000002.txt
"""
rows = F.parse_daily_index(IDX)
check("daily index: three data rows", len(rows), 3)
check("daily index: fields", (rows[0]["form"], rows[0]["name"], rows[0]["cik"], rows[0]["date"], rows[0]["path"]),
      ("4", "ACME CORP", 1234, "2026-11-18", "edgar/data/1234/0001234567-26-000011.txt"))
check("daily index: a form type with a space and slash", rows[2]["form"], "SCHEDULE 13D/A")
check("accession and folder", F.acc_and_dir(rows[0]["path"]), ("0001234567-26-000011", "https://www.sec.gov/Archives/edgar/data/1234/000123456726000011"))

FORM4 = """<ownershipDocument xmlns="http://www.sec.gov/edgar/ownership"><issuer><issuerCik>0000001234</issuerCik><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
<aff10b5One>0</aff10b5One>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>2026-11-16</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>25.50</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>2026-11-16</value></transactionDate><transactionCoding><transactionCode>A</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>5000</value></transactionShares><transactionPricePerShare><value>0</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>2026-11-17</value></transactionDate><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>200</value></transactionShares><transactionPricePerShare><value>26</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""
r4 = F.form4_rows(FORM4, "0001234567-26-000011", "2026-11-18")
check("form 4: grants dropped, P and S kept", [(r["code"], r["shares"], r["price"], r["value"]) for r in r4], [("P", 1000.0, 25.5, 25500.0), ("S", 200.0, 26.0, 5200.0)])
check("form 4: owner, role, symbol, plan flag", (r4[0]["owner"], r4[0]["role"], r4[0]["symbol"], r4[0]["plan_10b5_1"]), ("DOE JANE", "CFO", "ACME", False))
check("form 4: broken XML gives no rows", F.form4_rows("<ownershipDocument>", "x", "2026-11-18"), [])
check("form 4: 10b5-1 flag", F.form4_rows(FORM4.replace("<aff10b5One>0", "<aff10b5One>1"), "a", "2026-11-18")[1]["plan_10b5_1"], True)


def buy(owner, date, value=1000.0, sym="ACME", code="P", plan=False):
    return {"symbol": sym, "owner": owner, "date": date, "code": code, "value": value, "plan_10b5_1": plan}


ASOF = "2026-11-19"
s = F.insider_summary([buy("A", "2026-11-10"), buy("B", "2026-11-05"), buy("C", "2026-10-25")], ASOF)["ACME"]
check("cluster: 3 different buyers inside 30 days", (s["distinct_buyers_30d"], s["cluster"]), (3, True))
s = F.insider_summary([buy("A", "2026-11-10"), buy("A", "2026-11-11"), buy("B", "2026-11-05")], ASOF)["ACME"]
check("cluster: the same person twice is one buyer", (s["distinct_buyers_30d"], s["cluster"], s["n_buys"]), (2, False, 3))
s = F.insider_summary([buy("A", "2026-11-10"), buy("B", "2026-11-05"), buy("C", "2026-10-15")], ASOF)["ACME"]
check("cluster: a buyer 35 days ago does not count", (s["distinct_buyers_30d"], s["cluster"]), (2, False))
s = F.insider_summary([buy("A", "2026-08-01")], ASOF)
check("older than 90 days is dropped", s, {})
s = F.insider_summary([buy("A", "2026-11-10", 500, code="S"), buy("A", "2026-11-10", 700, code="S", plan=True), buy("A", "2026-11-10", 300)], ASOF)["ACME"]
check("sales: 10b5-1 sales kept apart", (s["sold"], s["sold_10b5_1"], s["bought"]), (500.0, 700.0, 300.0))

check("name normalisation", (F.norm_name("NVIDIA CORP"), F.norm_name("NVIDIA CORPORATION COM")), ("NVIDIA", "NVIDIA"))
check("name normalisation keeps distinctive words", F.norm_name("Advanced Micro Devices, Inc."), "ADVANCED MICRO DEVICES")

TABLE = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable><nameOfIssuer>NVIDIA CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066G104</cusip><value>1000000</value>
<shrsOrPrnAmt><sshPrnamt>10000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
<infoTable><nameOfIssuer>NVIDIA CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066G104</cusip><value>500000</value>
<shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
<infoTable><nameOfIssuer>NVIDIA CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066G104</cusip><value>99</value>
<shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Put</putCall></infoTable>
<infoTable><nameOfIssuer>SOME NOTE</nameOfIssuer><titleOfClass>NOTE</titleOfClass><cusip>123456AB1</cusip><value>10</value>
<shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""
t = F.parse_13f_table(TABLE)
check("13F: four lines parsed", len(t), 4)
h = F.holdings_by_cusip(t)
check("13F: equity only, puts and notes excluded, duplicate lines summed", h, {"67066G104": {"issuer": "NVIDIA CORP", "shares": 15000.0, "value": 1500000.0}})

LIST = ("67066G104 " + "NVIDIA CORP".ljust(30) + "COM".ljust(27) + "\n"
        "037833100 " + "APPLE INC".ljust(30) + "COM".ljust(27) + "\n"
        "garbage line\n")
cl = F.parse_13f_list(LIST)
check("13(f) list: CUSIP to issuer", cl, {"67066G104": "NVIDIA CORP", "037833100": "APPLE INC"})
cmap, rep = F.map_to_universe(cl, {"NVDA": "NVIDIA CORP", "AAPL": "Apple Inc.", "MU": "MICRON TECHNOLOGY INC"})
check("mapping by normalised name", cmap, {"67066G104": "NVDA", "037833100": "AAPL"})
check("mapping report", (rep["mapped"], rep["universe"], rep["unmapped"]), (2, 3, ["MU"]))

cur = {"M1": {"C1": {"shares": 200.0, "value": 2.0}, "C2": {"shares": 50.0, "value": 1.0}, "C4": {"shares": 5.0, "value": 1.0}},
       "M2": {"C1": {"shares": 80.0, "value": 1.0}}}
pri = {"M1": {"C1": {"shares": 100.0}, "C2": {"shares": 80.0}, "C3": {"shares": 40.0}}, "M2": {"C1": {"shares": 80.0}}}
fs = F.funds_summary(cur, pri, {"C1": "X", "C2": "Y", "C3": "Z", "C4": "W"})
check("funds: X held by 2, one add (M1), one unchanged (M2)", (fs["X"]["holders"], fs["X"]["adds"], fs["X"]["cuts"]), (2, 1, 0))
check("funds: Y was cut", (fs["Y"]["holders"], fs["Y"]["cuts"]), (1, 1))
check("funds: Z exited, W new", (fs["Z"]["exited"], fs["W"]["new"]), (1, 1))

cur2 = {"M1": {"C1": {"issuer": "ALPHA", "shares": 200.0, "value": 800.0}, "C2": {"issuer": "BETA", "shares": 50.0, "value": 100.0},
               "C4": {"issuer": "DELTA", "shares": 5.0, "value": 100.0}},
        "M2": {"C1": {"issuer": "ALPHA", "shares": 80.0, "value": 400.0}, "C2": {"issuer": "BETA", "shares": 90.0, "value": 200.0}}}
pri2 = {"M1": {"C1": {"issuer": "ALPHA", "shares": 100.0, "value": 1.0}, "C2": {"issuer": "BETA", "shares": 80.0, "value": 1.0},
               "C3": {"issuer": "GAMMA", "shares": 40.0, "value": 300.0}},
        "M2": {"C1": {"issuer": "ALPHA", "shares": 80.0, "value": 1.0}, "C2": {"issuer": "BETA", "shares": 60.0, "value": 1.0}}}
mv = F.manager_view(cur2["M1"], pri2["M1"], {"C1": "AAA"}, top=2)
check("manager view: total, positions, top-10 share", (mv["total_value"], mv["n_positions"], mv["top10_share"]), (1000.0, 3, 100.0))
check("manager view: changes vs prior quarter", {r["issuer"]: r["change"] for r in mv["holdings"]}, {"ALPHA": "added", "BETA": "reduced"})
check("manager view: weight and change percent", (mv["holdings"][0]["weight_pct"], mv["holdings"][0]["change_pct"], mv["holdings"][0]["symbol"]), (80.0, 100.0, "AAA"))
check("manager view: exited position listed", [(e["issuer"], e["prior_value"]) for e in mv["exited"]], [("GAMMA", 300.0)])
check("manager view: the new position is outside top 2 and unmapped, so not kept", [r["issuer"] for r in mv["holdings"]], ["ALPHA", "BETA"])
mv0 = F.manager_view(cur2["M1"], None, {})
check("manager view: no prior quarter means no change labels", mv0["holdings"][0]["change"], None)
cw = F.crowd(cur2, pri2, {"C1": "AAA"})
byi = {r["issuer"]: r for r in cw["most_held"]}
check("crowd: ALPHA held by 2, one add (M1), one unchanged", (byi["ALPHA"]["holders"], byi["ALPHA"]["adds"], byi["ALPHA"]["cuts"]), (2, 1, 0))
check("crowd: BETA one add (M2) and one cut (M1)", (byi["BETA"]["adds"], byi["BETA"]["cuts"]), (1, 1))
check("crowd: most held order", [r["issuer"] for r in cw["most_held"]][:2], ["ALPHA", "BETA"])
check("crowd: most added includes DELTA (new) and the adds", sorted(r["issuer"] for r in cw["most_added"]), ["ALPHA", "BETA", "DELTA"])
check("crowd: most reduced includes the exit GAMMA and the BETA cut", sorted(r["issuer"] for r in cw["most_reduced"]), ["BETA", "GAMMA"])
check("crowd: counts", (cw["n_managers"], cw["n_with_prior"]), (2, 2))
check("figi pick: the US composite listing wins", F.figi_pick([{"ticker": "NVDA", "exchCode": "UN", "name": "NVIDIA CORP", "securityType": "Common Stock"},
                                                              {"ticker": "NVDA", "exchCode": "US", "name": "NVIDIA CORP", "securityType": "Common Stock"}]),
      {"ticker": "NVDA", "name": "NVIDIA CORP", "type": "Common Stock"})
check("figi pick: no data, no ticker", (F.figi_pick(None), F.figi_pick([{"exchCode": "US"}])), (None, None))
fc = {"C1": {"ticker": "AAA", "asked": "2026-11-01"}, "C2": {"ticker": None, "asked": "2026-11-01"}, "C3": {"ticker": None, "asked": "2026-09-01"}}
check("figi wanted: new ones and old misses, not fresh misses or hits", F.figi_wanted(["C1", "C2", "C3", "C4"], fc, "2026-11-19"), ["C3", "C4"])
hr = F.holdings_rows(cur2, pri2, {"C1": "AAA"})
check("holdings rows: all held names, most held first", [(r["name"], r["holders"]) for r in hr], [("ALPHA", 2), ("BETA", 2), ("DELTA", 1)])
check("holdings rows: ticker, adds, cuts, new", {r["name"]: (r["ticker"], r["adds"], r["cuts"], r["new"]) for r in hr},
      {"ALPHA": ("AAA", 1, 0, 0), "BETA": (None, 1, 1, 0), "DELTA": (None, 0, 0, 1)})
check("holdings rows: exited positions are not in the held list", [r["name"] for r in hr if r["name"] == "GAMMA"], [])
big = {"C%d" % i: {"issuer": "N%d" % i, "shares": 10.0, "value": 100.0 - i} for i in range(6)}
mk = F.manager_view(big, {}, {"C5": "ZZZ"}, top=2, keep={"C5"})
check("manager view keeps the top N plus only the universe names beyond it", [r["cusip"] for r in mk["holdings"]], ["C0", "C1", "C5"])

D13 ="<edgarSubmission><formData><coverPageHeader><reportingPersonName>BIG HOLDER LP</reportingPersonName></coverPageHeader><percentOfClass>7.4</percentOfClass></formData></edgarSubmission>"
check("13D: stake and filer when present", F.parse_13d(D13, "SCHEDULE 13D"), {"stake_pct": 7.4, "filer": "BIG HOLDER LP"})
check("13D: unknown layout stays None", F.parse_13d("<x/>", "SC 13D"), {"stake_pct": None, "filer": None})
check("index url", F.daily_index_url(dt.date(2026, 11, 18)), "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261118.idx")
print("PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)

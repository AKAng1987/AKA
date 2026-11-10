"""check_gap_shock.py -- checks for gap_shock.py. Fixtures only; no network, no S3."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gap_shock as G  # noqa: E402
from positions_calendar import open_risk  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


# position value as a percent of the book: US book = 600 / 0.0025 = 240,000
us = {"symbol": "AAA", "book": "US", "_state": "planned", "entry": 100.0, "stop": 95.0, "shares": 240}   # 24,000 = 10%
check("book value is derived from 1R = 0.25%", (G.BOOK_VALUE["US"], G.BOOK_VALUE["PH"]), (240000.0, 10000000.0))
check("position is 10% of the US book", round(G.position_pct(us), 6), 10.0)
check("one name, beta 1, -5% shock loses 5% of its position value (0.5% of the book)", round(G.gap_loss_pct([us], {"AAA": 1.0}, -0.05), 6), 0.5)
check("beta 2 doubles it", round(G.gap_loss_pct([us], {"AAA": 2.0}, -0.05), 6), 1.0)
short = {**us, "symbol": "BBB", "side": "short"}
check("a hedged pair (long and short, same value, beta 1) nets out", round(G.gap_loss_pct([us, short], {"AAA": 1.0, "BBB": 1.0}, -0.05), 9), 0.0)
check("open position uses its fill, not the entry", round(G.position_pct({**us, "_state": "open", "_fill": 110.0}), 6), 11.0)
check("missing shares or price is unknown, not zero", (G.position_pct({**us, "shares": None}), G.position_pct({**us, "entry": None})), (None, None))

# beta on a synthetic pair of series
random.seed(7)
idx = {f"2026-01-{i:03d}": random.gauss(0, 0.01) for i in range(300)}
name2 = {d: 2.0 * r for d, r in idx.items()}
b, n, fb = G.beta(name2, idx)
check("beta of a name that moves twice the index is 2.0", (round(b, 6), n, fb), (2.0, 250, False))
short_hist = {d: r for d, r in list(idx.items())[:80]}
check("history under 120 sessions falls back to 1.0 and is marked", G.beta(short_hist, idx), (1.0, 80, True))
bars = {s: [[f"d{i:03d}", 1, 1, 1, 100 * (1.001 ** i), 0] for i in range(40)] for s in ("A", "B", "C")}
check("PSEi proxy needs enough members on a date", G.proxy_index(bars, ["A", "B", "C"], min_members=4), {})
check("PSEi proxy averages the members' returns", round(G.proxy_index(bars, ["A", "B", "C"], min_members=3)["d001"], 6), 0.001)
check("a +-40% jump is dropped as bad data", G.to_returns([("a", 1.0), ("b", 2.0), ("c", 2.1)]).get("b"), None)

# stress: PH book 1.5% at the stops, observed multiple 1.04 (from the study)
def ph(sym, entry, stop, shares):
    return {"symbol": sym, "book": "PH", "_state": "planned", "entry": entry, "stop": stop, "shares": shares}
live = [ph("ALI", 15.5, 15.0, 50000), ph("JFC", 145, 138, 3571), ph("BLOOM", 1.92, 1.8, 208333)]
risk = open_risk(live)
betas = {"ALI": (1.0, 250, False), "JFC": (0.5, 250, False), "BLOOM": (1.0, 60, True)}
st = G.stress(live, risk, betas, observed=(1.04, True))
b = st["books"]["PH"]
at = risk["PH"]["all_pct"]
check("multiples are the at-stops risk times 1.04, 1.5 and 2", (b["mult"]["observed"], b["mult"]["1.5x"], b["mult"]["2x"]),
      (round(at * 1.04, 2), round(at * 1.5, 2), round(at * 2.0, 2)))
check("all PH names are one cluster", list(b["clusters"]), ["PH"])
check("a cluster over 1.0% at the stops is flagged", b["clusters"]["PH"]["flag"], at > 1.0)
g5 = sum(G.position_pct(p) * betas[p["symbol"]][0] * 0.05 for p in live)
check("gap -5% is the sum of beta x shock x position value", b["gap"]["-5%"], round(g5, 2))
check("gap loss grows with the shock", b["gap"]["-3%"] < b["gap"]["-5%"] < b["gap"]["-8%"], True)

one = [ph("ALI", 15.5, 15.0, 20000)]   # 0.5 x 20,000 = 10,000 = 0.4R = 0.10%
sub = G.stress(one, open_risk(one), {}, observed=(1.04, True))["books"]["PH"]["clusters"]["PH"]
check("under the sub-cap is not flagged", (sub["pct"], sub["flag"]), (0.1, False))
edge = [ph("ALI", 15.5, 15.0, 50000), ph("JGS", 17.5, 16.5, 25000)]   # 1.0R + 1.0R... = 0.25% + 0.25%
at4 = [ph(f"N{i}", 10.0, 9.0, 25000) for i in range(4)]               # 4 x 1R = 1.00% exactly
c4 = G.stress(at4, open_risk(at4), {}, observed=(1.04, True))["books"]["PH"]["clusters"]["PH"]
check("exactly 1.00% is not over the line", (c4["pct"], c4["flag"]), (1.0, False))

# US names cluster by the plan's own field, else the CGI theme, else alone
usl = [{**us, "symbol": "X1", "cluster": "Saas"}, {**us, "symbol": "X2"}, {**us, "symbol": "X3"}]
check("US cluster: own field, then theme, then alone", [G.cluster_of(p, {"X2": "biotech"}) for p in usl], ["Saas", "biotech", "X3"])

# unknown risk is shown as at least, never as zero
unk = [{**ph("ALI", 15.5, 15.0, 50000)}, {**ph("NOSTOP", 10.0, None, 1000)}]
su = G.stress(unk, open_risk(unk), {}, observed=(1.04, True))["books"]["PH"]
check("an unknown-risk name is carried, not zeroed", su["unknown"], ["NOSTOP"])

# output: labelled, no size or order wording, no balance
txt = "\n".join(G.stress_lines(st))
check("fallback beta is labelled", "1.0 assumed for BLOOM" in txt, True)
check("H9 is labelled small sample", "H9, small sample" in txt, True)
check("states it warns only", "does not block a plan or suggest a size" in txt, True)
bad = [w for w in ("buy", "sell", "reduce", "cut size", "should", "balance", "10,000,000", "240,000") if w in txt.lower().replace("does not block a plan or suggest a size", "")]
check("no advice or balance words in the output", bad, [])
check("observed multiple falls back when the study file is missing", G.observed_multiple("/nonexistent.json"), (1.04, False))

print(f"FAILS: {fails}")
sys.exit(1 if fails else 0)

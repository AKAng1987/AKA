"""check_watch_table.py -- checks for watch_table.py. Fixtures only; the real mirror is never written."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_table as W  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))


today = dt.date(2026, 11, 10)
# lists run together with no newline, HTML around them, a tag in brackets, a stop word, an empty list, a Stops line
html1 = ("<div>Some words, no lists here.</div><div>Movers: Sector ETFs XLU XLV<br>Setup: HTFL (pullback), AMN (pullback), ICLR (pullback)"
         " PH Movers: SM&nbsp;MER ICT<br>PH Setup: ICT (reset) Stops: tight</div>")
html2 = "<div>Movers: APP AI SE</div><div>Setup: PH Movers: BDO PGOLD</div><div>PH Setup: MER (reset)</div>"
old = "<div>Movers: ZZZ</div>"
j = [{"id": "a", "ts": "2026-11-05T01:00:00Z", "entry_date": "2026-11-05", "body": html1},
     {"id": "b", "ts": "2026-11-06T09:00:00Z", "text": html2},
     {"id": "c", "ts": "2026-10-01T01:00:00Z", "body": old}]

lists = W.parse_lists(html1)
check("four wanted lists found in order", [x[0] for x in lists], ["Movers", "Setup", "PH Movers", "PH Setup"])
check("a label ends the previous list (no newline needed)", [x[1].split()[0] for x in lists if x[0] == "PH Movers"], ["SM"])
check("Stops is not a wanted list", [x[0] for x in W.parse_lists("Stops: AAA BBB")], [])

res = W.collect(j, today)
n = res["names"]
check("5 Nov tags kept", [(s, n[s]["tag"]) for s in ("HTFL", "AMN", "ICLR")], [("HTFL", "pullback"), ("AMN", "pullback"), ("ICLR", "pullback")])
check("ICT tagged reset, PH book", (n["ICT"]["tag"], n["ICT"]["book"]), ("reset", "PH"))
check("PH movers found", sorted(s for s, r in n.items() if r["book"] == "PH" and "PH Movers" in r["lists"]), ["BDO", "ICT", "MER", "PGOLD", "SM"])
check("entry older than 14 days is left out", "ZZZ" in n, False)
check("first named date is the earliest entry", n["MER"]["first"], dt.date(2026, 11, 5))
check("stop word AI goes to not matched, not to names", ("AI" in n, "AI" in res["unmatched"]), (False, True))
check("entries with lists counted", res["entries"], 2)
check("entry_date preferred over ts", W.entry_day({"entry_date": "2026-03-14", "ts": "2026-11-03T00:00:00Z"}), dt.date(2026, 3, 14))

# EMA and percent facts on a hand series
ser = [float(x) for x in range(1, 41)]   # 1..40
f = W.facts_from_closes(ser)
k = 2 / 21
e = sum(ser[:20]) / 20
for v in ser[20:]:
    e = v * k + e * (1 - k)
check("vs 20-day EMA on a hand series", round(f["vs20"], 4), round((40 / e - 1) * 100, 4))
check("1W and 1M changes use 5 and 21 sessions back", (round(f["w"], 2), round(f["m"], 2)), (round((40 / 35 - 1) * 100, 2), round((40 / 19 - 1) * 100, 2)))
check("too few bars gives no EMA", W.ema([1.0] * 5, 20), None)

# build: no ranking words, plan flag, no price source, theme, PH estimate label
ph = {"scanner": [{"s": "ICT", "c": 809, "d": -1.0, "w": -3.6, "m": -5.8, "vs50": -7.3}],
      "earnings": {"next": [{"s": "ICT", "date": "2026-11-11", "basis": "filed 2025-11-11 last year"}]}}
themes = {"themes": [{"theme": "biotech / healthcare", "stage": "late", "legs": [{"symbol": "HTFL"}]}]}
txt = W.build(today, res, {"HTFL"}, ph, themes, (49.5, "2026-11-09"), {"HTFL": ser, "AMN": None}, ["Pullback entries do better."])
check("estimate is labelled", "(estimate, filed 2025-11-11 last year)" in txt, True)
check("theme and stage shown for a US name", "theme: biotech / healthcare, stage late" in txt, True)
check("live plan flagged by symbol only", "HTFL (pullback)" in txt and "plan: yes" in txt, True)
check("name without a price source says so", txt.count("no price source") >= 1, True)
check("MMFI and the labelled context line shown", ("MMFI (S&P above its 50-day) today: 50" in txt, "small sample, a guide" in txt), (True, True))
body = "\n".join(txt.splitlines()[1:-2])   # header and disclaimer say "no rank, no score" themselves
check("no ranking or advice words in the table", [w for w in ("best", "rank", "buy", "sell", "score") if w in body.lower()], [])

print(f"FAILS: {fails}")
sys.exit(1 if fails else 0)

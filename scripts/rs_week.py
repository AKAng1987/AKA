"""
rs_week.py -- "this week's relative strength" for the daily/weekly brief (zero tokens).

For every theme leg (cgi-vercel api/themes_data.py THEMES) and every ticker in the CGI sector-ETF group,
return over 5 sessions minus SPY's, ranked; prints top and bottom N with the 20-session figure beside it
(5d > 20d/4 pace = strengthening, else fading). A description of the week, not a forecast.

  python3 scripts/rs_week.py [--top 10] [--json out.json] [--cgi ~/cgi-vercel]
Prices: Yahoo daily closes via the chart endpoint, standard library only; CGI aliases mapped to the listed ETF.
"""
from __future__ import annotations
import argparse, json, os, re, sys

ALIAS = {"URANIUM": "URA", "LITHIUM": "LIT", "NICKEL": "NIKL", "COAL": "COAL"}


def universe(cgi: str) -> dict[str, str]:
    """ticker -> label (theme name, or 'sector ETF')."""
    src = open(os.path.join(cgi, "api", "themes_data.py")).read().replace("import axis_drivers as ad", "ad = None")
    ns: dict = {}
    exec(compile(src, "themes_data.py", "exec"), ns)
    out = {}
    for theme, legs in ns["THEMES"].items():
        for s in legs:
            out.setdefault(s, theme)
    dd = open(os.path.join(cgi, "api", "dashboard_data.py")).read()
    m = re.search(r'"SECTOR ETF"\s*:\s*\[(.*?)\]', dd, re.S)
    for s in re.findall(r'"([A-Z0-9]{2,10})"', m.group(1) if m else ""):
        out.setdefault(s, "sector ETF")
    return out


def closes(sym: str) -> list[tuple[str, float]]:
    """[(date, close)] for ~3 months from Yahoo's chart endpoint; standard library only (cloud-safe)."""
    import datetime as dt, time, urllib.request
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
            ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
            return [(dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), c) for t, c in zip(ts, cl) if c]
        except Exception:
            time.sleep(2 * (attempt + 1))
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json")
    ap.add_argument("--cgi", default=os.path.expanduser("~/cgi-vercel"))
    a = ap.parse_args()
    uni = universe(a.cgi)
    ysym = {s: ALIAS.get(s, s) for s in uni}
    px = {y: closes(y) for y in sorted(set(ysym.values()) | {"SPY"})}
    spy = px["SPY"]
    asof = spy[-1][0]
    s5, s20 = spy[-1][1] / spy[-6][1] - 1, spy[-1][1] / spy[-21][1] - 1
    rows, skipped = [], []
    for s, lab in uni.items():
        c = px.get(ysym[s]) or []
        if len(c) < 21 or c[-1][0] != asof:
            skipped.append(s); continue   # stale or short: skipped, not guessed
        r5 = (c[-1][1] / c[-6][1] - 1 - s5) * 100
        r20 = (c[-1][1] / c[-21][1] - 1 - s20) * 100
        rows.append({"ticker": s, "label": lab, "rs5": round(r5, 2), "rs20": round(r20, 2),
                     "trend": "strengthening" if r5 > r20 / 4 else "fading"})
    rows.sort(key=lambda r: r["rs5"], reverse=True)
    out = {"asof": asof, "spy_5d_pct": round(s5 * 100, 2), "n": len(rows),
           "skipped": sorted(skipped), "top": rows[:a.top], "bottom": rows[-a.top:][::-1],
           "note": "5-session return minus SPY, in % points; a description of the week, not a forecast"}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    print(f"as of {asof}  SPY 5d {out['spy_5d_pct']:+.2f}%  ({len(rows)} tickers; skipped {len(skipped)}: {' '.join(sorted(skipped))})")
    for title, lst in (("TOP", out["top"]), ("BOTTOM", out["bottom"])):
        print(title)
        for r in lst:
            print(f"  {r['ticker']:8} {r['rs5']:+6.2f}  20d {r['rs20']:+6.2f}  {r['trend']:13} {r['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

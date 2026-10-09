"""
brief_data.py -- everything the daily brief needs that does not need a connector (zero tokens).

  python3 scripts/brief_data.py --out brief.json [--cgi ~/cgi-vercel] [--holdings 10]

Writes one JSON with:
  themes       full /api/themes rows (stage, onset, age, runway, RS, price, legs running x/y), megatrend first
  idle         themes with no active run
  watch        idle themes (with gain_vs_spy_to_cross_pct: how much the leg must beat SPY to cross its trend) whose best leg is within 3% of its 200d RS trend, or crossed above it in the last
               20 days, or sits in this week's RS top 10 -- candidates BEFORE onset ("watch, not a signal")
  rs_week      rs_week.py output (5d vs SPY, 20d beside it)
  holdings     top-N holdings per theme in force (from the running legs, via api/etf_holdings.py), for the
               earnings slide: {ticker: [theme, ...]}
The connector parts (TradingView quotes, economic and earnings calendars, news) are fetched by /cgi-deck and merged.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, subprocess, sys, tempfile, urllib.request

API = "https://cgi-vercel.vercel.app"


def get(path: str) -> dict:
    for attempt in range(3):
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "cgi-brief"})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception:
            if attempt == 2:
                raise
            import time; time.sleep(20)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cgi", default=os.path.expanduser("~/cgi-vercel"))
    ap.add_argument("--holdings", type=int, default=10)
    a = ap.parse_args()

    th = get("/api/themes")
    themes = th["themes"]
    running = [t for t in themes if t.get("n_running")]
    idle = [t for t in themes if not t.get("n_running")]
    # megatrends oldest first; rotations youngest first (early runs are the ones to watch)
    mega = sorted([t for t in running if t.get("class") == "megatrend"], key=lambda t: t.get("onset") or "")
    rot = sorted([t for t in running if t.get("class") != "megatrend"], key=lambda t: t.get("age_days") or 0)
    running = mega + rot

    tmp = os.path.join(tempfile.mkdtemp(), "rs.json")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "rs_week.py"), "--json", tmp,
                    "--cgi", a.cgi], check=True, capture_output=True)
    rs = json.load(open(tmp))
    top_rs = {r["ticker"] for r in rs["top"]}

    today = dt.date.fromisoformat(th["as_of"])
    watch = []
    for t in idle:
        best = max(t["legs"], key=lambda l: l.get("rs_vs_trend_pct", -999))
        gap = best.get("rs_vs_trend_pct")
        la = best.get("last_above")
        recent = la and (today - dt.date.fromisoformat(la)).days <= 20
        why = []
        if gap is not None and gap >= -3:
            why.append(f"{best['symbol']} RS {gap:+.1f}% vs its 200d trend")
        if recent:
            why.append(f"{best['symbol']} was above trend on {la}")
        hot = [l["symbol"] for l in t["legs"] if l["symbol"] in top_rs]
        if hot:
            why.append("top-10 week: " + " ".join(hot))
        if why:
            need = round((1 / (1 + gap / 100) - 1) * 100, 1) if gap is not None else None
            watch.append({"theme": t["theme"], "best_leg": best["symbol"], "rs_vs_trend_pct": gap,
                          "gain_vs_spy_to_cross_pct": need,
                          "last_above": la, "why": "; ".join(why)})
    watch.sort(key=lambda w: -(w["rs_vs_trend_pct"] or -99))

    sys.path.insert(0, os.path.join(a.cgi, "api"))
    import etf_holdings
    holdings: dict[str, list[str]] = {}
    prov = {}
    for t in running:
        legs = [l["symbol"] for l in t["legs"] if l["status"] == "running"]
        try:
            names, p = etf_holdings.top_constituents(legs, n=a.holdings)
        except Exception as e:   # a holdings source being down must not stop the brief
            names, p = [], [{"error": str(e)[:200]}]
        prov[t["theme"]] = p
        for n in names:
            holdings.setdefault(n, []).append(t["theme"])

    out = {"as_of": th["as_of"], "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "run_stats": th.get("run_stats"), "themes": running, "idle": [t["theme"] for t in idle],
           "watch": watch, "rs_week": rs, "holdings": holdings, "holdings_provenance": prov,
           "sources": {"themes": API + "/api/themes", "rs_week": "scripts/rs_week.py (Yahoo closes)",
                       "holdings": "api/etf_holdings.py (State Street / SEC N-PORT)"}}
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"as of {out['as_of']}: {len(running)} running, {len(idle)} idle, {len(watch)} to watch, "
          f"{len(holdings)} holdings names")
    for w in watch:
        print("  watch:", w["theme"], "-", w["why"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

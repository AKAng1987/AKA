"""
cgi_watchlists.py -- Print the two TradingView watchlists CGI maintains:

  CGI · now            top of the BACKTEST leaderboard for the current regime
  CGI · if next flips  same for the regime we land in if the next release
                       flips its axis

Output is JSON (name, description, symbols in EXCHANGE:TICKER form) for
the TradingView MCP watchlist tools to write; the lists are overwritten
in place so the count of lists stays small.

TradingView watchlist ids (created 2026-09-19):
  CGI · now            347463015
  CGI · if next flips  347463028
Refresh = run this, then in a Claude Code session with the TradingView
connector: remove-from-watchlist (old symbols) + add-to-watchlist (new),
or update-watchlist for the description.

Usage:
  .venv/bin/python scripts/cgi_watchlists.py            # both, top 15
  TOP=20 MIN_OCC=5 .venv/bin/python scripts/cgi_watchlists.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "cgi-vercel" / "api"))
import backtest_data as bd  # noqa: E402
import markov_data as md  # noqa: E402
import release_calendar as cal  # noqa: E402

TOP = int(os.environ.get("TOP", "15"))
MIN_OCC = int(os.environ.get("MIN_OCC", "5"))

# CGI ticker -> TradingView symbol. Exchanges follow the user's own lists.
TV = {
    "VIX": "TVC:VIX", "SPX": "TVC:SPX", "SPY": "AMEX:SPY", "QQQ": "NASDAQ:QQQ", "IWM": "AMEX:IWM", "DIA": "AMEX:DIA",
    "RUT": "TVC:RUT", "DXY": "TVC:DXY", "TLT": "NASDAQ:TLT", "IEF": "NASDAQ:IEF", "HYG": "AMEX:HYG", "LQD": "AMEX:LQD",
    "GLD": "AMEX:GLD", "GDX": "AMEX:GDX", "SLV": "AMEX:SLV", "USO": "AMEX:USO", "UNG": "AMEX:UNG", "DBC": "AMEX:DBC",
    "DBA": "AMEX:DBA", "USCI": "AMEX:USCI", "CPER": "AMEX:CPER", "COPPER": "COMEX:HG1!", "GOLD": "TVC:GOLD", "SILVER": "TVC:SILVER", "USOIL": "TVC:USOIL",
    "BTC": "BITSTAMP:BTCUSD", "ETH": "BITSTAMP:ETHUSD",
    "USDSGD": "OANDA:USDSGD", "USDTHB": "OANDA:USDTHB", "USDCAD": "OANDA:USDCAD", "USDJPY": "FX:USDJPY",
    "USDCHF": "FX:USDCHF", "USDMXN": "FX:USDMXN", "USDTRY": "FX:USDTRY",
}


def tv_symbol(ticker: str, group: str) -> str:
    if ticker in TV:
        return TV[ticker]
    g = group.upper()
    if g == "FX":
        return f"FX_IDC:{ticker}"
    if g == "CRYPTO":
        return f"BITSTAMP:{ticker}USD"
    if "ETF" in g or g in ("SECTOR ETF", "COUNTRY ETF", "US EQUITIES"):
        return f"AMEX:{ticker}"
    return f"AMEX:{ticker}"


def leaderboard(c: int, g: int, min_occ: int) -> list[dict]:
    t = bd.build_table_response(c, g, min_occ=min_occ)
    rows = [r for r in t["rows"] if r.get("edge") is not None]
    rows.sort(key=lambda r: -r["edge"])
    return rows


def main() -> None:
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    cur = {m: md._load_model(f"{m}_US")[-1][1] for m in ("compass", "grid")}
    nxt = sorted(cal.next_releases(today), key=lambda r: r["date"])[0]
    axis, model = nxt["axis"], nxt["model"]
    s = cal.Q_TO_AXES[cur[model]][cal.SLOT_OF[axis]]
    dest = dict(cur)
    dest[model] = md._quadrant_with(cur[model], axis, 1 - s)

    out = []
    for name, reg, note in (
        ("CGI · now", cur, f"current regime"),
        ("CGI · if next flips", dest, f"if {nxt['type']} on {nxt['date']} flips {axis}"),
    ):
        min_occ = MIN_OCC
        rows = leaderboard(reg["compass"], reg["grid"], min_occ)
        if len(rows) < 5 and min_occ > 3:
            min_occ = 3
            rows = leaderboard(reg["compass"], reg["grid"], min_occ)
        rows = rows[:TOP]
        label = f"C{reg['compass']}G{reg['grid']}"
        desc = (f"{label} — {note}. BACKTEST leaderboard by edge, min {min_occ} occurrences, "
                f"refreshed {today} by CGI. " +
                " · ".join(f"{r['ticker']} {r['edge']:+.2f} ({r['occurrences']})" for r in rows))
        out.append({
            "name": name, "regime": label, "description": desc,
            "symbols": [f"###{label} · {note.upper()}"] + [tv_symbol(r["ticker"], r["group"]) for r in rows],
            "rows": [{k: r[k] for k in ("ticker", "group", "occurrences", "hit_rate", "edge", "avg_return_pct")} for r in rows],
        })
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

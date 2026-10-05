"""
fix_feed_orphans.py -- three series that had silently stopped updating.

  MAGS, UUP   registered under source=tradingview, which NO Lambda serves, and absent
              from series_write.ALLOWED, so the manual routine never touched them either:
              the same orphan failure as the breadth series. They go to yahoo_finance,
              which the table-driven yahoo-finance-updater already maintains, so they
              cost no tokens. Yahoo's closes were compared with our TradingView-sourced
              rows over 8+ overlapping days first: identical to the cent (one 0.07% print
              difference), so there is no scale change.

  URA         registered under marketstack TWICE, as URA and as URANIUM, both with
              source_symbol=URA. The updater keys its lookup on source_symbol, so the
              second registration overwrites the first and URA starved (last bar
              2026-09-01) while URANIUM kept updating. URA is the same Global X Uranium ETF
              counted twice in the universe; it is retired and its registration removed.

The yahoo updater fetches only the latest bar, so the gap since each series stopped is
backfilled here, in the updater's own row shape (OHLC + adj_close + volume).

  python3 scripts/fix_feed_orphans.py            # dry run: prints what it would do
  python3 scripts/fix_feed_orphans.py --apply
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import urllib.request

REGION = "ap-southeast-1"
PRICE, MSRC = "cmon-stage-backend-price-history", "cmon-stage-backend-metrics-source"
APPLY = "--apply" in sys.argv

MOVE = {
    "MAGS": "Roundhill Magnificent Seven ETF (Yahoo MAGS; was TradingView CBOE:MAGS, unmaintained)",
    "UUP": "Invesco DB US Dollar Index Bullish Fund (Yahoo UUP; was TradingView AMEX:UUP, unmaintained)",
}


def aws(*args: str) -> str:
    r = subprocess.run(["aws", *args, "--region", REGION, "--cli-read-timeout", "60"],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def last_date(sym: str) -> str:
    out = aws("dynamodb", "query", "--table-name", PRICE, "--key-condition-expression", "#s=:s",
              "--expression-attribute-names", '{"#s":"symbol"}',
              "--expression-attribute-values", json.dumps({":s": {"S": sym}}),
              "--no-scan-index-forward", "--limit", "1", "--query", "Items[0].date.S", "--output", "text")
    return out.strip()


def yahoo(sym: str) -> list[dict]:
    req = urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d",
        headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())["chart"]["result"][0]
    q, adj = d["indicators"]["quote"][0], d["indicators"]["adjclose"][0]["adjclose"]
    out = []
    for i, ts in enumerate(d["timestamp"]):
        if None in (q["close"][i], q["open"][i], q["high"][i], q["low"][i]):
            continue
        out.append({"date": dt.datetime.utcfromtimestamp(ts).date().isoformat(), "open": q["open"][i],
                    "high": q["high"][i], "low": q["low"][i], "close": q["close"][i],
                    "adj_close": adj[i], "volume": q["volume"][i] or 0})
    return out


def num(x) -> dict:
    return {"N": repr(float(x))}


def main() -> None:
    print(("APPLY" if APPLY else "DRY RUN") + "\n")
    for sym, desc in MOVE.items():
        have = last_date(sym)
        new = [b for b in yahoo(sym) if b["date"] > have]
        print(f"{sym}: last held {have}; Yahoo has {len(new)} newer bars "
              f"({new[0]['date'] + ' .. ' + new[-1]['date'] if new else 'none'})")
        for b in new:
            print(f"    {b['date']}  close={b['close']:.4f}")
            if APPLY:
                item = {"symbol": {"S": sym}, "date": {"S": b["date"]}, "source": {"S": "yahoo_finance"},
                        "source_symbol": {"S": sym}, **{k: num(b[k]) for k in
                        ("open", "high", "low", "close", "adj_close", "volume")}}
                aws("dynamodb", "put-item", "--table-name", PRICE, "--item", json.dumps(item))
        print(f"    registry: remove tradingview/{sym}; add yahoo_finance/{sym} (1D)")
        if APPLY:
            aws("dynamodb", "put-item", "--table-name", MSRC, "--item", json.dumps({
                "source": {"S": "yahoo_finance"}, "symbol": {"S": sym}, "source_symbol": {"S": sym},
                "frequency": {"S": "1D"}, "description": {"S": desc}}))
            aws("dynamodb", "delete-item", "--table-name", MSRC,
                "--key", json.dumps({"source": {"S": "tradingview"}, "symbol": {"S": sym}}))

    print("\nURA: remove the duplicate marketstack registration (URANIUM keeps serving source ticker URA)")
    if APPLY:
        aws("dynamodb", "delete-item", "--table-name", MSRC,
            "--key", json.dumps({"source": {"S": "marketstack"}, "symbol": {"S": "URA"}}))
    print("\ndone" if APPLY else "\n(dry run: nothing written)")


if __name__ == "__main__":
    main()

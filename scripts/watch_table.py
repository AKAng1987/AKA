"""
watch_table.py -- his own Flow lists with facts beside the names. Zero tokens. Descriptive only: no rank, no score.

Reads (read-only): ~/pulse/mirror/journal (the Movers / Setup / PH Movers / PH Setup lines of the last 14 days), the Pulse plans,
the public CGI site (/api/ph, /api/themes, /tape for MMFI) and Yahoo daily bars for US names. Writes out/watch_table.txt.
It never writes to Flow or Pulse and never ranks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import html
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WINDOW_DAYS = 14
LABELS = ("PH Movers", "PH Setup", "Movers", "Setup", "Holdings", "Holding", "Stops")
WANTED = {"Movers": "US", "Setup": "US", "PH Movers": "PH", "PH Setup": "PH"}
LABEL_RE = re.compile(r"(?<![A-Za-z])(PH Movers|PH Setup|Movers|Setup|Holdings|Holding|Stops)\s*:", re.I)
TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9.]{1,5})\b(?:\s*\(([^)]{1,30})\))?")
CONTEXT_FILE = os.path.expanduser("~/pulse/PLAN_CURRENT.md")
CONTEXT_KEYS = ("Pullback entries (within 3% of 20EMA)", "MMFI<30")


class _Text(HTMLParser):
    BREAK = {"br", "div", "p", "li", "tr", "h1", "h2", "h3"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BREAK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def plain(text: str) -> str:
    p = _Text()
    p.feed(text or "")
    return html.unescape("".join(p.out)).replace("\xa0", " ")


def entry_day(j: dict) -> dt.date | None:
    for k in ("entry_date", "ts"):
        v = j.get(k)
        if v:
            try:
                return dt.date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return None


def parse_lists(text: str) -> list[tuple[str, str]]:
    """[(label, list text)] for the four wanted labels. A label ends the previous list, so lists need no newline between them."""
    t = plain(text)
    marks = [(m.start(), m.end(), m.group(1)) for m in LABEL_RE.finditer(t)]
    out = []
    for i, (s, e, lab) in enumerate(marks):
        canon = next((x for x in LABELS if x.lower() == lab.lower()), lab)
        if canon not in WANTED:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(t)
        seg = t[e:end].split("\n")[0]          # a list is one line
        out.append((canon, seg.strip()))
    return out


# Uppercase words that are not tickers. Everything else uppercase in a list line is a ticker he named; if no price source
# knows it, the table says "no price source" instead of dropping it.
NOT_TICKERS = {"PH", "US", "AND", "THE", "FOR", "OF", "IN", "ON", "TO", "AI", "ETF", "ETFS", "IPO", "CPI", "GDP", "FOMC", "PCE",
               "USD", "PHP", "EMA", "MACD", "RS", "NA", "ALL", "NEW", "TOP", "OIL", "BSP", "SPY500", "OK", "NO", "IF", "AT", "BY"}


def parse_names(seg: str, stop: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Uppercase tickers with an optional (tag). Stop-listed words go to not matched."""
    names, bad = [], []
    for m in TOKEN_RE.finditer(seg):
        sym, tag = m.group(1), (m.group(2) or "").strip()
        (bad if sym in stop else names).append(sym if sym in stop else (sym, tag))
    return names, bad


def collect(journal: list[dict], today: dt.date, stop: set[str] | None = None) -> dict:
    """{symbol: {book, tag, first, last, lists}} plus not matched, from entries in the last WINDOW_DAYS."""
    cut = today - dt.timedelta(days=WINDOW_DAYS)
    found: dict[str, dict] = {}
    unmatched: dict[str, int] = {}
    n_entries = 0
    for j in sorted(journal, key=lambda x: str(entry_day(x) or "")):
        d = entry_day(j)
        if d is None or d < cut or d > today:
            continue
        lists = parse_lists((j.get("body") or "") + "\n" + (j.get("text") or ""))
        if not lists:
            continue
        n_entries += 1
        for lab, seg in lists:
            names, bad = parse_names(seg, NOT_TICKERS if stop is None else stop)
            for sym, tag in names:
                r = found.setdefault(sym, {"book": WANTED[lab], "tag": "", "first": d, "last": d, "lists": set()})
                r["last"] = d
                r["lists"].add(lab)
                if tag:
                    r["tag"] = tag            # his newest tag wins
            for b in bad:
                unmatched[b] = unmatched.get(b, 0) + 1
    return {"names": found, "unmatched": unmatched, "entries": n_entries}


# ── facts ────────────────────────────────────────────────────────────────────────────────────────

def ema(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else (a / b - 1) * 100


def facts_from_closes(closes: list[float]) -> dict:
    last = closes[-1]
    e = ema(closes, 20)
    return {"close": last, "vs20": pct(last, e), "w": pct(last, closes[-6]) if len(closes) > 5 else None,
            "m": pct(last, closes[-22]) if len(closes) > 21 else None}


def yahoo_closes(sym: str) -> list[float] | None:
    try:
        req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=6mo&interval=1d",
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        c = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        c = [x for x in c if x is not None]
        return c if len(c) >= 22 else None
    except Exception:  # noqa: BLE001
        return None


def theme_map(themes: dict | None) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for t in (themes or {}).get("themes", []):
        for leg in t.get("legs", []):
            out.setdefault(leg.get("symbol"), (t.get("theme"), t.get("stage")))
    return out


def fmt(x: float | None, d: int = 1, plus: bool = True) -> str:
    return "n/a" if x is None else (f"{x:+.{d}f}" if plus else f"{x:.{d}f}")


def context_lines(path: str = CONTEXT_FILE) -> list[str]:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        for sent in re.split(r"(?<=[A-Za-z%)])\.\s+", ln.strip().lstrip("- ").strip()):
            if any(k in sent for k in CONTEXT_KEYS) and sent not in out:
                out.append(sent.rstrip(".") + ".")
    return out


def build(today: dt.date, found: dict, plan_syms: set[str], ph: dict | None, themes: dict | None, mmfi: tuple | None,
          prices: dict[str, list[float] | None], context: list[str]) -> str:
    scan = {r["s"]: r for r in (ph or {}).get("scanner", [])}
    nxt = {r["s"]: r for r in ((ph or {}).get("earnings", {}) or {}).get("next", [])}
    tm = theme_map(themes)
    names = found["names"]
    L = [f"WATCH TABLE  {today.isoformat()}  (your own Flow lists, last {WINDOW_DAYS} days; facts only, no rank, no score)", ""]
    for book, title in (("US", "US names"), ("PH", "PH names")):
        rows = sorted([(s, r) for s, r in names.items() if r["book"] == book], key=lambda x: (x[1]["first"], x[0]))
        L.append(f"{title}: {len(rows)}")
        for s, r in rows:
            tag = f" ({r['tag']})" if r["tag"] else ""
            days = (today - r["first"]).days
            plan = "plan: yes" if s in plan_syms else "plan: none"
            if book == "PH":
                p = scan.get(s)
                if p:
                    px = f"close {p['c']:g}  1D {fmt(p.get('d'))}%  1W {fmt(p.get('w'))}%  1M {fmt(p.get('m'))}%  vs 50d {fmt(p.get('vs50'))}% (20-day EMA not in the public data)"
                else:
                    px = "no price source"
                e = nxt.get(s)
                res = f"next results {e['date']} (estimate, {e['basis']})" if e else "next results: not in the next window"
                th = ""
            else:
                c = prices.get(s)
                if c:
                    f = facts_from_closes(c)
                    px = f"close {f['close']:.2f}  vs 20d EMA {fmt(f['vs20'])}%  1W {fmt(f['w'])}%  1M {fmt(f['m'])}%"
                else:
                    px = "no price source"
                res = "next results: not checked"
                t = tm.get(s)
                th = f"  theme: {t[0]}, stage {t[1]}" if t else "  theme: none holds it"
            L.append(f"  {s}{tag}  first named in window {r['first'].isoformat()} ({days}d ago)  {plan}")
            L.append(f"    {px}")
            L.append(f"    {res}{th}")
        L.append("")
    unm = found["unmatched"]
    L.append(f"Not matched ({len(unm)}): " + (", ".join(f"{k}" for k in sorted(unm)) if unm else "none"))
    L.append("")
    if mmfi:
        L.append(f"MMFI (S&P above its 50-day) today: {mmfi[0]:.0f}  (as of {mmfi[1]})")
    else:
        L.append("MMFI today: not read")
    for c in context:
        L.append(f"Your 2026 trades, small sample, a guide: {c}")
    L.append("")
    L.append("Describes your own lists. Not a ranking and not a signal.")
    return "\n".join(L) + "\n"


# ── io ───────────────────────────────────────────────────────────────────────────────────────────

def load_journal(mirror: str) -> list[dict]:
    out = []
    for f in sorted(glob.glob(os.path.join(mirror, "journal", "*.json"))):
        try:
            out.append(json.load(open(f)))
        except ValueError:
            continue
    return out


def read_mmfi() -> tuple[float, str] | None:
    """MMFI (S&P above its 50-day) from the public daily brief."""
    try:
        from cgi_health import _get
        h = _get("/api/brief?cadence=daily")
        m = re.search(r'"symbol":"MMFI","label":"[^"]*","as_of":"(\d{4}-\d{2}-\d{2})","value":([0-9.]+)', h)
        return (float(m.group(2)), m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str] | None = None, echo: bool = True) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default=os.path.expanduser("~/pulse/mirror"))
    ap.add_argument("--out", default=os.path.expanduser("~/market-dashboard/out/watch_table.txt"))
    ap.add_argument("--today", default=None)
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    from positions_calendar import fetch_json, live_plans, load_mirror
    plans, results, _meta = load_mirror(a.mirror)
    plan_syms = {p.get("symbol") for p in live_plans(plans, results)}
    ph = themes = None
    for name in ("ph", "themes"):
        try:
            v = fetch_json("/api/" + name)
            ph, themes = (v, themes) if name == "ph" else (ph, v)
        except Exception:  # noqa: BLE001
            pass
    found = collect(load_journal(a.mirror), today)
    prices = {s: yahoo_closes(s) for s, r in found["names"].items() if r["book"] == "US"}
    text = build(today, found, plan_syms, ph, themes, read_mmfi(), prices, context_lines())
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(text)
    if echo:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
gap_shock.py -- what the book I actually hold loses if the market gaps against it and the stops do not hold. Zero tokens.

It predicts nothing about direction. It warns; it never blocks a plan and never suggests a size. Everything is in percent of the
book and R: position values are used inside this script only and no balance is read or written (the book is the constant the
README already states: 1R = 0.25% of the book, so US$600 -> US$240,000 and PHP 25,000 -> PHP 10,000,000).

Three numbers per book:
  1. risk at the stops (from positions_calendar.open_risk)
  2. the same if stops fail by a multiple: the observed one (H9, small sample), 1.5x and 2x (planning assumptions)
  3. an index gap of -3 / -5 / -8% times each name's beta, with no stops at all
Betas are labelled with their window and n (one decimal). A name with too little history uses 1.0 and is marked.
Clusters: names that fall together count as one bet. Start simple: all PH names are one cluster; US names group by the plan's
own `cluster` field, else by the CGI theme that holds them, else stand alone. A cluster over 1.0% at the stops is flagged.
"""
from __future__ import annotations

import json
import os
from typing import Callable

ONE_R = {"PH": 25000.0, "US": 600.0}
PCT_PER_R = 0.25
BOOK_VALUE = {b: ONE_R[b] / (PCT_PER_R / 100.0) for b in ONE_R}   # derived from the stated 1R rule; never a read balance
SHOCKS = (-0.03, -0.05, -0.08)
STOP_MULTS_PLANNING = (1.5, 2.0)
OBSERVED_FALLBACK = 1.04                                           # H9 on his 2026 trades (mean of the nine full stop-outs)
CLUSTER_SUBCAP = 1.0                                               # % of book at the stops (NOT_BUILT item 9)
MIN_N = 120                                                        # sessions needed before a name's own beta is used
WINDOW = 250
STOPFAIL = os.path.expanduser("~/pulse/mirror/analysis/stopfail.json")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def observed_multiple(path: str = STOPFAIL) -> tuple[float, bool]:
    """(multiple, from_study). H9's mean overshoot on his planned trades; the fallback is labelled."""
    try:
        m = _num(json.load(open(path))["primary_planned"]["mean"])
        if m and 0.5 < m < 5:
            return round(m, 2), True
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return OBSERVED_FALLBACK, False


# ── beta ─────────────────────────────────────────────────────────────────────────────────────────

def to_returns(series: list[tuple[str, float]]) -> dict[str, float]:
    """{date: simple return} from [(date, close)] sorted by date. A return beyond +-40% is treated as bad data and dropped."""
    out = {}
    for (d0, c0), (d1, c1) in zip(series, series[1:]):
        if c0 and c0 > 0 and c1 and c1 > 0:
            r = c1 / c0 - 1
            if abs(r) <= 0.40:
                out[d1] = r
    return out


def beta(name: dict[str, float], index: dict[str, float], window: int = WINDOW, min_n: int = MIN_N) -> tuple[float, int, bool]:
    """(beta, n aligned sessions, is_fallback). Slope of the name's returns on the index's over the newest `window` sessions."""
    dates = sorted(set(name) & set(index))[-window:]
    n = len(dates)
    if n < min_n:
        return 1.0, n, True
    x = [index[d] for d in dates]
    y = [name[d] for d in dates]
    mx, my = sum(x) / n, sum(y) / n
    var = sum((a - mx) ** 2 for a in x)
    if var <= 0:
        return 1.0, n, True
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / var, n, False


def proxy_index(bars: dict[str, list], members: list[str], min_members: int = 15) -> dict[str, float]:
    """PSEi stand-in: the equal-weight average daily return of the PSEi members in the EDGE bars (no free PSEi history exists).
    bars[sym] = [[date, o, h, l, c, value], ...]."""
    by_date: dict[str, list[float]] = {}
    for s in members:
        rows = bars.get(s) or []
        for d, r in to_returns([(row[0], row[4]) for row in rows]).items():
            by_date.setdefault(d, []).append(r)
    return {d: sum(v) / len(v) for d, v in by_date.items() if len(v) >= min_members}


# ── positions ────────────────────────────────────────────────────────────────────────────────────

def position_pct(p: dict) -> float | None:
    """Signed position value as % of its book: shares x (fill if open else entry) / book. Short = negative. None if unknown."""
    book = p.get("book") or "US"
    shares = _num(p.get("shares"))
    px = _num(p.get("_fill")) if p.get("_state") == "open" else _num(p.get("entry"))
    if not shares or shares <= 0 or not px or px <= 0:
        return None
    sign = -1.0 if str(p.get("side") or "").lower() == "short" else 1.0
    return sign * shares * px / BOOK_VALUE.get(book, BOOK_VALUE["US"]) * 100.0


def cluster_of(p: dict, theme_of: dict[str, str] | None = None) -> str:
    """PH: one cluster. US: the plan's own `cluster`, else the CGI theme holding the name, else the name alone."""
    sym = p.get("symbol", "?")
    if (p.get("book") or "US") == "PH":
        return "PH"
    return (p.get("cluster") or (theme_of or {}).get(sym) or sym)


def gap_loss_pct(live: list[dict], betas: dict[str, float], shock: float) -> float:
    """% of the book lost if the book's index gaps by `shock` (negative) and no stop holds: sum of beta x shock x position%."""
    loss = 0.0
    for p in live:
        v = position_pct(p)
        if v is not None:
            loss += -shock * betas.get(p.get("symbol"), 1.0) * v
    return loss


def stress(live: list[dict], risk: dict, betas: dict[str, tuple[float, int, bool]], theme_of: dict[str, str] | None = None,
           observed: tuple[float, bool] | None = None) -> dict:
    """risk = positions_calendar.open_risk(live). Pure: no network, no files except the optional H9 file."""
    obs = observed or observed_multiple()
    out = {"observed": obs, "books": {}}
    for book in ("PH", "US"):
        mine = [p for p in live if (p.get("book") or "US") == book]
        if not mine:
            continue
        r = risk[book]
        at = r["all_pct"]
        b = {x: betas.get(x, (1.0, 0, True))[0] for x in (p.get("symbol") for p in mine)}
        clusters: dict[str, dict] = {}
        for item in r["items"]:
            p = next((q for q in mine if q.get("symbol") == item["symbol"]), None)
            if p is None:
                continue
            c = clusters.setdefault(cluster_of(p, theme_of), {"names": [], "pct": 0.0})
            c["names"].append(item["symbol"])
            if not item["unknown"]:
                c["pct"] += item["r"] * PCT_PER_R
        out["books"][book] = {
            "at_stops": at, "unknown": list(r["unknown"]),
            "mult": {"observed": round(at * obs[0], 2), "1.5x": round(at * 1.5, 2), "2x": round(at * 2.0, 2)},
            "gap": {f"{int(s * 100)}%": round(gap_loss_pct(mine, b, s), 2) for s in SHOCKS},
            "betas": {s: betas.get(s, (1.0, 0, True)) for s in b},
            "clusters": {k: {"names": v["names"], "pct": round(v["pct"], 2), "flag": v["pct"] > CLUSTER_SUBCAP + 1e-9} for k, v in clusters.items()},
        }
    return out


def stress_lines(st: dict) -> list[str]:
    obs, from_study = st["observed"]
    L: list[str] = []
    for book, b in st["books"].items():
        inc = f" (at least: {len(b['unknown'])} name(s) with unknown risk)" if b["unknown"] else ""
        mult = (f"stops fail x{obs:g} {b['mult']['observed']:.2f}% (H9{'' if from_study else ', fallback'}, small sample)"
                f" | x1.5 {b['mult']['1.5x']:.2f}% | x2 {b['mult']['2x']:.2f}%")
        gap = " | ".join(f"gap {k} {v:.2f}%" for k, v in b["gap"].items())
        L.append(f"STRESS {book}  at stops {b['at_stops']:.2f}%{inc} | {mult}")
        L.append(f"       {book} no stops: {gap} of the book (index gap x each name's beta)")
        fb = [s for s, (_, _, fall) in b["betas"].items() if fall]
        ok = [(s, be, n) for s, (be, n, fall) in b["betas"].items() if not fall]
        bs = "  ".join(f"{s} {be:.1f} (n {n})" for s, be, n in ok)
        L.append(f"       betas ({'PSEi proxy: equal-weight of the 30 members' if book == 'PH' else 'SPX'}, up to {WINDOW} sessions): "
                 f"{bs or 'none from data'}" + (f"; 1.0 assumed for {', '.join(fb)} (history under {MIN_N} sessions or no bars)" if fb else ""))
        for k, c in b["clusters"].items():
            L.append(f"       cluster {k}: {len(c['names'])} name(s), {c['pct']:.2f}% at the stops"
                     + (f"  ! over the {CLUSTER_SUBCAP:.1f}% sub-cluster line" if c["flag"] else "")
                     + (f" ({', '.join(c['names'])})" if book == "US" else ""))
    if L:
        L.append("       Warns only; it does not block a plan or suggest a size. Percent of book; betas are descriptive and move.")
    return L


# ── data (network and S3; kept out of the pure functions above) ──────────────────────────────────

def load_ph_bars() -> dict:
    """The EDGE daily bars the PH builder already stored (300 sessions, liquid names plus COL and CTS). Read-only."""
    import boto3
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket="cmon-stage-backend-369568916817-ap-southeast-1-reports", Key="Cache/macro/ph/bars.json")
    return json.loads(obj["Body"].read()).get("bars", {})


def yahoo_series(sym: str) -> list[tuple[str, float]] | None:
    import datetime as dt
    import urllib.request
    try:
        req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d",
                                     headers={"User-Agent": "Mozilla/5.0"})
        r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
        ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
        return [(dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), c) for t, c in zip(ts, cl) if c is not None]
    except Exception:  # noqa: BLE001
        return None


def compute(live: list[dict], risk: dict, ph: dict | None, theme_of: dict[str, str] | None,
           ph_bars: Callable[[], dict] = load_ph_bars, us_series: Callable[[str], list | None] = yahoo_series) -> dict:
    """Fetch what is needed and return stress(). A source that fails leaves its names at beta 1.0, marked."""
    betas: dict[str, tuple[float, int, bool]] = {}
    ph_syms = [p["symbol"] for p in live if (p.get("book") or "US") == "PH"]
    if ph_syms:
        try:
            bars = ph_bars()
            members = [r["s"] for r in (ph or {}).get("scanner", []) if r.get("psei")]
            idx = proxy_index(bars, members)
            for s in ph_syms:
                rows = bars.get(s)
                betas[s] = beta(to_returns([(r[0], r[4]) for r in rows]), idx) if rows and idx else (1.0, 0, True)
        except Exception:  # noqa: BLE001
            pass
    us_syms = [p["symbol"] for p in live if (p.get("book") or "US") == "US"]
    if us_syms:
        spx = us_series("^GSPC")
        for s in us_syms:
            ser = us_series(s) if spx else None
            betas[s] = beta(to_returns(ser), to_returns(spx)) if ser and spx else (1.0, 0, True)
    return stress(live, risk, betas, theme_of)

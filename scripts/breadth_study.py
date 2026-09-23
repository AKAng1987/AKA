"""
breadth_study.py -- TECHNICALS factor: what breadth says about the next
move, and how the breadth measures relate to each other.

Series (all TradingView INDEX:, loaded into price-history, ~2006-11 ->):
  MMTW  % of S&P 500 above its 20-day MA    -- short-term participation
  MMFI  % above 50-day MA                   -- intermediate
  MMTH  % above 200-day MA                  -- long-term / structural
  HIGN  NYSE new 52-week highs (count)
  LOWN  NYSE new 52-week lows (count)
  net new highs = HIGN - LOWN, with 8- and 20-day EMAs (Caruso's read)

Questions answered:
  1. correlation matrix between the breadth measures and SPY forward returns
  2. P(SPY up / drawdown) conditioned on each measure's decile today
  3. the user's specific question -- "can the market handle the selloff,
     short term or long term" -- as: given MMTW low, does the outcome
     differ when MMTH is high vs low?
  4. net-new-high EMA cross (8 over 20) as a state, and what follows

Usage:
  .venv/bin/python scripts/breadth_study.py            # full report
  OUT=docs/BREADTH_STUDY.md .venv/bin/python scripts/breadth_study.py
"""
from __future__ import annotations

import bisect
import datetime as dt
import os
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path.home() / "cgi-vercel" / "api"))

import axis_drivers as ad  # noqa: E402  (reuses _load_close)

OUT = Path(os.environ.get("OUT", HERE / "docs" / "BREADTH_STUDY.md"))
HORIZONS = (5, 20, 60)


def load(sym: str) -> dict[str, float]:
    d, c = ad._load_close(sym)
    return dict(zip(d, c))


def ema(vals: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = (sum((x - mx) ** 2 for x in xs) ** 0.5) * (sum((y - my) ** 2 for y in ys) ** 0.5)
    return num / dx if dx else 0.0


def deciles(vals: list[float], k: int = 5) -> list[float]:
    s = sorted(vals)
    return [s[int(len(s) * i / k)] for i in range(1, k)]


def bucket(x: float, bounds: list[float]) -> int:
    return bisect.bisect_right(bounds, x)


def main() -> None:
    print("loading series...", flush=True)
    series = {s: load(s) for s in ("MMTW", "MMFI", "MMTH", "HIGN", "LOWN")}
    spy = load("SPY")
    dates = sorted(set(series["MMTW"]) & set(series["MMFI"]) & set(series["MMTH"])
                   & set(series["HIGN"]) & set(series["LOWN"]) & set(spy))
    print(f"{len(dates)} aligned trading days: {dates[0]} -> {dates[-1]}", flush=True)

    net = [series["HIGN"][d] - series["LOWN"][d] for d in dates]
    e8, e20 = ema(net, 8), ema(net, 20)
    px = [spy[d] for d in dates]

    feats: dict[str, list[float]] = {
        "MMTW (20d)": [series["MMTW"][d] for d in dates],
        "MMFI (50d)": [series["MMFI"][d] for d in dates],
        "MMTH (200d)": [series["MMTH"][d] for d in dates],
        "net new highs": net,
        "NNH 8ema": e8,
        "NNH 8-20 spread": [a - b for a, b in zip(e8, e20)],
    }
    # forward returns and forward max drawdown
    fwd: dict[int, list[float | None]] = {}
    dd: dict[int, list[float | None]] = {}
    for h in HORIZONS:
        fwd[h] = [((px[i + h] / px[i] - 1) * 100 if i + h < len(px) else None) for i in range(len(px))]
        dd[h] = [((min(px[i + 1:i + h + 1]) / px[i] - 1) * 100 if i + h < len(px) else None) for i in range(len(px))]

    L: list[str] = [f"# Breadth study — TECHNICALS factor ({dt.date.today()})", "",
                    f"{len(dates)} aligned trading days, {dates[0]} → {dates[-1]}. "
                    "Breadth from TradingView INDEX: symbols; SPY from price-history. "
                    "Forward returns are SPY close-to-close; drawdown is the worst close "
                    "within the horizon, relative to today.", ""]

    # 1. correlation matrix
    L += ["## How the measures relate", "",
          "| | " + " | ".join(feats) + " |", "|---|" + "---|" * len(feats)]
    for a, va in feats.items():
        row = [f"{pearson(va, vb):+.2f}" for vb in feats.values()]
        L.append(f"| **{a}** | " + " | ".join(row) + " |")
    L += ["", "Correlation of each measure with SPY forward return:", "",
          "| measure | " + " | ".join(f"+{h}d" for h in HORIZONS) + " |",
          "|---|" + "---|" * len(HORIZONS)]
    for name, v in feats.items():
        cells = []
        for h in HORIZONS:
            pairs = [(x, y) for x, y in zip(v, fwd[h]) if y is not None]
            cells.append(f"{pearson([p[0] for p in pairs], [p[1] for p in pairs]):+.2f}")
        L.append(f"| {name} | " + " | ".join(cells) + " |")

    # 2. quintile conditioning
    L += ["", "## What follows each level", "",
          "Quintiles of each measure over the full history; "
          "`up` = share of days where SPY was higher N days later; "
          "`ret` = mean forward return; `dd` = mean worst drawdown inside the window.", ""]
    for name, v in feats.items():
        b = deciles(v, 5)
        L += [f"### {name}", "",
              "| quintile | range | n | " + " | ".join(f"up {h}d | ret {h}d | dd {h}d" for h in HORIZONS) + " |",
              "|---|---|---|" + "---|" * (3 * len(HORIZONS))]
        lo = min(v)
        for q in range(5):
            hi = b[q] if q < 4 else max(v)
            idx = [i for i in range(len(v)) if bucket(v[i], b) == q]
            cells = []
            for h in HORIZONS:
                rs = [fwd[h][i] for i in idx if fwd[h][i] is not None]
                ds = [dd[h][i] for i in idx if dd[h][i] is not None]
                if rs:
                    cells += [f"{sum(1 for r in rs if r > 0) / len(rs):.0%}", f"{st.mean(rs):+.1f}%", f"{st.mean(ds):+.1f}%"]
                else:
                    cells += ["—", "—", "—"]
            L.append(f"| Q{q + 1} | {lo:.0f} to {hi:.0f} | {len(idx)} | " + " | ".join(cells) + " |")
            lo = hi
        L.append("")

    # 3. the user's question: short-term washout, structural support or not
    L += ["## \"Can the market handle the selloff?\"", "",
          "Short-term breadth washed out (MMTW in its bottom quintile), split by "
          "whether long-term participation is still intact (MMTH above/below its median).", ""]
    mmtw, mmth = feats["MMTW (20d)"], feats["MMTH (200d)"]
    b20 = deciles(mmtw, 5)
    med200 = st.median(mmth)
    L += ["| MMTW | MMTH | n | " + " | ".join(f"up {h}d | ret {h}d | dd {h}d" for h in HORIZONS) + " |",
          "|---|---|---|" + "---|" * (3 * len(HORIZONS))]
    for lbl, sel in (("bottom quintile", lambda i: bucket(mmtw[i], b20) == 0),
                     ("top quintile", lambda i: bucket(mmtw[i], b20) == 4)):
        for mlbl, msel in ((f"above median ({med200:.0f})", lambda i: mmth[i] >= med200),
                           (f"below median ({med200:.0f})", lambda i: mmth[i] < med200)):
            idx = [i for i in range(len(mmtw)) if sel(i) and msel(i)]
            cells = []
            for h in HORIZONS:
                rs = [fwd[h][i] for i in idx if fwd[h][i] is not None]
                ds = [dd[h][i] for i in idx if dd[h][i] is not None]
                if rs:
                    cells += [f"{sum(1 for r in rs if r > 0) / len(rs):.0%}", f"{st.mean(rs):+.1f}%", f"{st.mean(ds):+.1f}%"]
                else:
                    cells += ["—", "—", "—"]
            L.append(f"| {lbl} | {mlbl} | {len(idx)} | " + " | ".join(cells) + " |")

    # 4. net-new-high EMA cross
    L += ["", "## Net new highs: 8-EMA vs 20-EMA", "",
          "State = 8-day EMA above or below the 20-day EMA of (new highs − new lows), "
          "and the first 10 days after a fresh cross.", ""]
    above = [e8[i] > e20[i] for i in range(len(dates))]
    cross_up = [i for i in range(1, len(dates)) if above[i] and not above[i - 1]]
    cross_dn = [i for i in range(1, len(dates)) if not above[i] and above[i - 1]]
    L += ["| state | n | " + " | ".join(f"up {h}d | ret {h}d | dd {h}d" for h in HORIZONS) + " |",
          "|---|---|" + "---|" * (3 * len(HORIZONS))]
    for lbl, idx in (("8 > 20 (any day)", [i for i in range(len(dates)) if above[i]]),
                     ("8 < 20 (any day)", [i for i in range(len(dates)) if not above[i]]),
                     (f"fresh cross up ({len(cross_up)})", cross_up),
                     (f"fresh cross down ({len(cross_dn)})", cross_dn)):
        cells = []
        for h in HORIZONS:
            rs = [fwd[h][i] for i in idx if fwd[h][i] is not None]
            ds = [dd[h][i] for i in idx if dd[h][i] is not None]
            if rs:
                cells += [f"{sum(1 for r in rs if r > 0) / len(rs):.0%}", f"{st.mean(rs):+.1f}%", f"{st.mean(ds):+.1f}%"]
            else:
                cells += ["—", "—", "—"]
        L.append(f"| {lbl} | {len(idx)} | " + " | ".join(cells) + " |")

    # today
    i = len(dates) - 1
    L += ["", "## Today", "", f"as of {dates[i]}:", ""]
    for name, v in feats.items():
        b = deciles(v, 5)
        L.append(f"- **{name}** {v[i]:.1f} — quintile Q{bucket(v[i], b) + 1}")
    L.append(f"- net new highs 8-EMA {e8[i]:.1f} vs 20-EMA {e20[i]:.1f} "
             f"({'8 above 20' if e8[i] > e20[i] else '8 below 20'})")
    OUT.write_text("\n".join(L) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

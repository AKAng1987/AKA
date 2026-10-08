"""
tv_chart_svg.py -- draw a candlestick chart (SVG) from TradingView bars, for slides and brief pages.

The TradingView connector has no screenshot tool, but it returns the exact bars TradingView charts
(`mcp-tv-get-ohlcv`). This draws them in one consistent style so every deck looks the same, with no
dependencies (standard library only), so it also runs in a cloud routine.

Input: the JSON the connector returns (or just its "bars" list: [{t, o, h, l, c, v}, ...]) on stdin or --in.
Output: an SVG on stdout or --out, sized for a slide (default 1600x900) and under the Slides 52 KB limit
(long histories are resampled to weekly bars automatically).

  python3 scripts/tv_chart_svg.py --in bars.json --out chart.svg --title "SPX" \
      --ema 50,200 --mark 2026-08-03:buy --mark 2026-09-02:sell --theme light

Options:
  --title TEXT          shown top-left (symbol, timeframe)
  --ema N,N             exponential moving averages to overlay (e.g. 20,50,200)
  --mark DATE:LABEL     vertical marker (repeatable): buy, sell, or any short label; DATE = YYYY-MM-DD
  --hline PRICE:LABEL   horizontal level (repeatable), e.g. a stop or a breakout level
  --theme light|dark    light = white slide (default, matches my decks); dark = CGI dashboard
  --width/--height      pixels
Labels are drawn as SVG text in a system font; in a Slides deck put the caption in a <p>, not in the chart.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

THEMES = {
    "light": {"bg": "#fbfbfa", "grid": "#e3e6ea", "axis": "#5b6472", "up": "#0a8f3c", "down": "#c62828",
              "ema": ["#41527a", "#a85400", "#8a6510"], "mark": "#41527a", "text": "#0f172a"},
    "dark": {"bg": "#020617", "grid": "#1a2340", "axis": "#94a3b8", "up": "#00C851", "down": "#FF4444",
             "ema": ["#8b9dc3", "#FF8C00", "#FCD34D"], "mark": "#8b9dc3", "text": "#f1f5f9"},
}
MAX_CANDLES = 260   # keeps the SVG well under 52 KB


def _date(t) -> dt.date:
    return dt.datetime.fromtimestamp(int(t), dt.timezone.utc).date() if isinstance(t, (int, float)) else dt.date.fromisoformat(str(t)[:10])


def _weekly(bars: list[dict]) -> list[dict]:
    out, cur = [], None
    for b in bars:
        k = _date(b["t"]).isocalendar()[:2]
        if cur is None or cur["k"] != k:
            if cur: out.append(cur)
            cur = {"k": k, "t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            cur.update(h=max(cur["h"], b["h"]), l=min(cur["l"], b["l"]), c=b["c"])
    if cur: out.append(cur)
    return out


def _ema(vals: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for i, v in enumerate(vals):
        out.append(v if i == 0 else v * k + out[-1] * (1 - k))
    return out


def render(bars, title="", emas=(), marks=(), hlines=(), theme="light", width=1600, height=900) -> str:
    bars = [b for b in bars if all(b.get(x) is not None for x in ("o", "h", "l", "c"))]
    if len(bars) > MAX_CANDLES:
        bars = _weekly(bars)[-MAX_CANDLES:]
    th = THEMES[theme]
    pad_l, pad_r, pad_t, pad_b = 24, 96, 64, 48
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    closes = [b["c"] for b in bars]
    lines = {n: _ema(closes, n) for n in emas}
    lo = min(min(b["l"] for b in bars), *(min(v) for v in lines.values()), *(p for p, _ in hlines)) if (lines or hlines) else min(b["l"] for b in bars)
    hi = max(max(b["h"] for b in bars), *(max(v) for v in lines.values()), *(p for p, _ in hlines)) if (lines or hlines) else max(b["h"] for b in bars)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.04, hi + span * 0.04
    y = lambda p: pad_t + h * (hi - p) / (hi - lo)
    step = w / len(bars)
    x = lambda i: pad_l + step * (i + 0.5)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-label="{title} chart">',
         f'<rect width="{width}" height="{height}" fill="{th["bg"]}"/>']
    for i in range(6):   # price grid + labels
        p = lo + (hi - lo) * i / 5
        yy = y(p)
        o.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + w}" y2="{yy:.1f}" stroke="{th["grid"]}" stroke-width="1"/>')
        o.append(f'<text x="{pad_l + w + 10}" y="{yy + 6:.1f}" font-family="system-ui,Arial" font-size="18" fill="{th["axis"]}">{p:,.2f}</text>')
    dates = [_date(b["t"]) for b in bars]
    for i in range(0, len(bars), max(1, len(bars) // 6)):   # date labels
        o.append(f'<text x="{x(i):.1f}" y="{height - 16}" font-family="system-ui,Arial" font-size="18" fill="{th["axis"]}" text-anchor="middle">{dates[i].strftime("%b %d %Y" if len(bars) < 120 else "%b %Y")}</text>')
    bw = max(1.0, step * 0.6)
    for i, b in enumerate(bars):
        col = th["up"] if b["c"] >= b["o"] else th["down"]
        o.append(f'<line x1="{x(i):.1f}" y1="{y(b["h"]):.1f}" x2="{x(i):.1f}" y2="{y(b["l"]):.1f}" stroke="{col}" stroke-width="1.5"/>')
        top, bot = y(max(b["o"], b["c"])), y(min(b["o"], b["c"]))
        o.append(f'<rect x="{x(i) - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(1.0, bot - top):.1f}" fill="{col}"/>')
    for k, (n, vals) in enumerate(lines.items()):
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        c = th["ema"][k % len(th["ema"])]
        o.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="2.5"/>')
        o.append(f'<text x="{pad_l + 8 + 110 * k}" y="{pad_t - 14}" font-family="system-ui,Arial" font-size="18" fill="{c}">EMA {n}</text>')
    for p, lab in hlines:
        o.append(f'<line x1="{pad_l}" y1="{y(p):.1f}" x2="{pad_l + w}" y2="{y(p):.1f}" stroke="{th["mark"]}" stroke-width="2" stroke-dasharray="8 6"/>')
        o.append(f'<text x="{pad_l + 8}" y="{y(p) - 8:.1f}" font-family="system-ui,Arial" font-size="18" fill="{th["mark"]}">{lab} {p:,.2f}</text>')
    for d, lab in marks:
        i = min(range(len(dates)), key=lambda j: abs((dates[j] - d).days))
        c = th["up"] if lab.lower() == "buy" else th["down"] if lab.lower() == "sell" else th["mark"]
        o.append(f'<line x1="{x(i):.1f}" y1="{pad_t}" x2="{x(i):.1f}" y2="{pad_t + h}" stroke="{c}" stroke-width="2" stroke-dasharray="4 4"/>')
        o.append(f'<text x="{x(i) + 6:.1f}" y="{pad_t + 22}" font-family="system-ui,Arial" font-size="20" font-weight="700" fill="{c}">{lab}</text>')
    if title:
        o.append(f'<text x="{pad_l}" y="{pad_t - 36}" font-family="system-ui,Arial" font-size="26" font-weight="700" fill="{th["text"]}">{title}</text>')
    o.append("</svg>")
    return "\n".join(o)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp"); ap.add_argument("--out"); ap.add_argument("--title", default="")
    ap.add_argument("--ema", default=""); ap.add_argument("--mark", action="append", default=[])
    ap.add_argument("--hline", action="append", default=[]); ap.add_argument("--theme", default="light", choices=THEMES)
    ap.add_argument("--width", type=int, default=1600); ap.add_argument("--height", type=int, default=900)
    a = ap.parse_args()
    data = json.load(open(a.inp) if a.inp else sys.stdin)
    bars = data["bars"] if isinstance(data, dict) else data
    if not bars:
        print("no bars", file=sys.stderr); return 2
    svg = render(bars, a.title, [int(n) for n in a.ema.split(",") if n],
                 [(dt.date.fromisoformat(m.split(":", 1)[0]), m.split(":", 1)[1]) for m in a.mark],
                 [(float(hl.split(":", 1)[0]), hl.split(":", 1)[1] if ":" in hl else "") for hl in a.hline],
                 a.theme, a.width, a.height)
    (open(a.out, "w").write(svg) if a.out else sys.stdout.write(svg))
    print(f"{len(svg) / 1024:.1f} KB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

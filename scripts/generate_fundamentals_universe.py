"""
Regenerate docs/FUNDAMENTALS_UNIVERSE.md from the live API.

The doc had drifted: it claimed "152 of 162 names resolve" and listed 123
tickers, while /api/fundamentals returned a different universe and different
medians -- copper showed -7.9pp in the doc against +4.4pp live, and eleven
themes disagreed. Two records of one thing, one of them hand-maintained,
which is the same failure shape as the bundled HUD_GROUPS copy that drifted
38 tickers and as GOLD sitting outside the commodities map.

The API is the source of truth: it derives constituents from what each
theme's ETFs actually hold. The doc is a rendering of it, and is now
generated rather than edited. Anything you would want to hand-write here
belongs in the API instead.

Reads the PUBLIC proxy, so no token is needed and what gets written is
exactly what a reader of the public API would see.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

API = "https://cgi-vercel.vercel.app/api/fundamentals"
OUT = Path(__file__).resolve().parent.parent / "docs" / "FUNDAMENTALS_UNIVERSE.md"


def pp(v, n=None) -> str:
    if v is None:
        return "—"
    s = f"{v:+.1f}pp"
    return f"{s} (n={n})" if n else s


def main(write: bool) -> None:
    with urllib.request.urlopen(API, timeout=120) as r:
        d = json.load(r)

    themes = d["themes"]
    cov = d.get("coverage", {})
    live = [t for t in themes if t.get("is_live")]
    names = sorted({c for t in themes for c in t["constituents"]})

    L = []
    L.append("# FUNDAMENTALS universe — what is covered, and why")
    L.append("")
    L.append(f"**GENERATED FILE — do not edit.** Produced by")
    L.append("`scripts/generate_fundamentals_universe.py` from the live")
    L.append("`/api/fundamentals`. The previous hand-maintained version drifted from")
    L.append("the API on eleven themes and on the name count; the API derives")
    L.append("constituents from what each theme's ETFs actually hold, so it is the")
    L.append("source of truth and this is a rendering of it.")
    L.append("")
    L.append(f"API as-of **{d.get('as_of')}** · regenerated **{dt.date.today().isoformat()}**")
    L.append("")
    if cov:
        parts = [f"{k} {v}" for k, v in cov.items() if isinstance(v, (int, float, str))]
        if parts:
            L.append("Coverage: " + " · ".join(parts))
            L.append("")
    L.append(f"{len(themes)} themes ({len(live)} live) · {len(names)} distinct names")
    L.append("")
    L.append("Personal holdings are deliberately NOT included: this repository and")
    L.append("`/api/fundamentals` are both public, so a position list here would be a")
    L.append("position list published.")
    L.append("")
    L.append("## Themes")
    L.append("")
    L.append("Medians are revenue acceleration in percentage points — the rate of")
    L.append("change of growth, not growth. `n` is how many constituents had enough")
    L.append("filings to contribute; a median over 2 names is reported as such rather")
    L.append("than hidden.")
    L.append("")
    L.append("| theme | constituents | quarterly median | annual median | source |")
    L.append("|---|---|---|---|---|")
    for t in themes:
        L.append("| {} | {} | {} | {} | {} |".format(
            t["theme"],
            " ".join(t["constituents"]) or "—",
            pp(t.get("median_revenue_acceleration_pp"), t.get("n_in_median")),
            pp(t.get("median_annual_acceleration_pp"), t.get("n_in_annual_median")),
            t.get("source", "—"),
        ))
    L.append("")
    stale = [t["theme"] for t in themes if not t.get("is_live")]
    if stale:
        L.append(f"**Not live ({len(stale)}):** " + ", ".join(stale))
        L.append("")
    L.append("## Limits")
    L.append("")
    for lim in d.get("limits", []) or []:
        L.append(f"- {lim}")
    text = "\n".join(L).rstrip() + "\n"

    if not write:
        print(text[:1600]); print(f"\n... [{len(text)} chars] — pass --write to save")
        return
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text)} chars, {len(themes)} themes, {len(names)} names)")


if __name__ == "__main__":
    main("--write" in sys.argv)

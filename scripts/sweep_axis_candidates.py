"""
sweep_axis_candidates.py -- Test candidate drivers for an axis against the
regime history using the same event study as the live "What moves each
axis" panel, without onboarding anything. FRED candidates are fetched
straight from FRED (cached in ~/.cache/cgi_sweep), existing symbols come
from price-history. Output: a ranked markdown report per axis.

Usage:
  .venv/bin/python scripts/sweep_axis_candidates.py inflation growth liquidity
  OUT=docs/CANDIDATE_SWEEP_2026-09-18.md .venv/bin/python scripts/sweep_axis_candidates.py inflation

Read-only against AWS. Reuses ~/cgi-vercel/api/axis_drivers.py so the
numbers match what the panel would show if the driver were adopted.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path.home() / "cgi-vercel" / "api"))

import macro_data  # noqa: E402
import axis_drivers as ad  # noqa: E402
import markov_data as md  # noqa: E402
import release_calendar as cal  # noqa: E402

CACHE = Path.home() / ".cache" / "cgi_sweep"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = Path(os.environ.get("OUT", HERE / "docs" / f"CANDIDATE_SWEEP_{dt.date.today().isoformat()}.md"))

# FRED series pulled directly (not in price-history). Everything else in a
# spec is read from price-history like the live panel does.
FRED = {
    # inflation
    "PPIFIS": "PPI final demand (m)", "PPIACO": "PPI all commodities (m)", "IR": "Import price index (m)",
    "CPILFESL": "Core CPI (m)", "PCEPILFE": "Core PCE (m)", "AHETPI": "Avg hourly earnings (m)",
    "T5YIE": "5y breakeven (d)", "T5YIFR": "5y5y fwd inflation (d)", "MICH": "Michigan 1y infl exp (m)",
    "DCOILWTICO": "WTI (d)", "DTWEXBGS": "Broad dollar (d)", "STICKCPIM157SFRBATL": "Sticky CPI m/m (m)",
    "CUSR0000SAH1": "CPI shelter (m)",
    # growth
    "ICSA": "Initial claims (w)", "CCSA": "Continuing claims (w)", "PAYEMS": "Payrolls (m)",
    "INDPRO": "Industrial production (m)", "PERMIT": "Building permits (m)", "HOUST": "Housing starts (m)",
    "RSXFS": "Retail sales ex autos (m)", "NEWORDER": "Core capex orders (m)", "AWHMAN": "Mfg weekly hours (m)",
    "UMCSENT": "Michigan sentiment (m)", "TCU": "Capacity utilization (m)", "CFNAI": "Chicago Fed activity (m)",
    "USSLIND": "Leading index (m)", "GDPNOW": "GDPNow (vintage) (q)", "JTSJOL": "Job openings (m)",
    "TOTALSA": "Vehicle sales (m)", "DGORDER": "Durable goods orders (m)",
    # liquidity
    "FEDFUNDS": "Effective fed funds (m)", "DFF": "Effective fed funds (d)",
    # Regional Fed surveys -- free stand-ins for the paywalled ISM series.
    # They print BEFORE ISM each month (Empire ~15th, Philly 3rd Thursday).
    "PPCDFSA066MSFRBPHI": "Philly Fed prices paid (m, 1968->)",
    "GACDFSA066MSFRBPHI": "Philly Fed general activity (m, 1968->)",
    "GAFDFSA066MSFRBPHI": "Philly Fed future activity (m, 1968->)",
    "PPCDISA066MSFRBNY": "Empire prices paid (m, 2001->)",
    "GACDISA066MSFRBNY": "Empire general conditions (m, 2001->)",
    "NOCDISA066MSFRBNY": "Empire new orders (m, 2001->)",
    "BACTSAMFRBDAL": "Dallas Fed business activity (m, 2004->)",
}

CANDIDATES: dict[str, list[tuple]] = {
    "inflation": [
        ("DBC 30d %", "DBC", "pct"), ("DBA 30d %", "DBA", "pct"), ("USO 30d %", "USO", "pct"),
        ("Copper 30d %", "COPPER", "pct"), ("WTI 30d %", "DCOILWTICO", "pct"),
        ("WTI 3m %", "DCOILWTICO", "pct@63"), ("Dollar 30d %", "DTWEXBGS", "pct"),
        ("10y breakeven 30d chg", "T10YIE", "diff"), ("10y breakeven (level)", "T10YIE", "level"),
        ("5y breakeven 30d chg", "T5YIE", "diff"), ("5y5y fwd 30d chg", "T5YIFR", "diff"),
        ("CPI y/y %", "CPIAUCSL", "yoy_pct"), ("CPI m/m %", "CPIAUCSL", "mom_pct"),
        ("CPI 3m %", "CPIAUCSL", "pct@3"), ("Core CPI y/y %", "CPILFESL", "yoy_pct"),
        ("Core CPI 3m %", "CPILFESL", "pct@3"), ("Core PCE y/y %", "PCEPILFE", "yoy_pct"),
        ("Shelter CPI y/y %", "CUSR0000SAH1", "yoy_pct"), ("Sticky CPI m/m", "STICKCPIM157SFRBATL", "level"),
        ("PPI final y/y %", "PPIFIS", "yoy_pct"), ("PPI final 3m %", "PPIFIS", "pct@3"),
        ("PPI commodities 3m %", "PPIACO", "pct@3"), ("Import prices y/y %", "IR", "yoy_pct"),
        ("Import prices 3m %", "IR", "pct@3"), ("Hourly earnings y/y %", "AHETPI", "yoy_pct"),
        ("Michigan 1y exp (level)", "MICH", "level"), ("Michigan 1y exp m/m", "MICH", "mom_diff"),
        ("Unemployment m/m chg", "UNRATE", "mom_diff"),
        # TradingView pulls (loaded via load_manual_series.py, source=tradingview)
        ("ISM mfg prices (level)", "ISM_MFG_PRICES", "level"),
        ("ISM mfg prices m/m", "ISM_MFG_PRICES", "mom_diff"),
        ("ISM mfg prices 3m chg", "ISM_MFG_PRICES", "diff@3"),
        ("ISM svc prices (level)", "ISM_SVC_PRICES", "level"),
        ("ISM svc prices m/m", "ISM_SVC_PRICES", "mom_diff"),
        ("ISM svc prices 3m chg", "ISM_SVC_PRICES", "diff@3"),
        ("USCI 30d %", "USCI", "pct"),
        ("Baltic Dry 4w %", "BDI", "pct@4"),
        ("Baltic Dry 13w %", "BDI", "pct@13"),
        ("Philly prices paid (level)", "PPCDFSA066MSFRBPHI", "level"),
        ("Philly prices paid 3m chg", "PPCDFSA066MSFRBPHI", "diff@3"),
        ("Philly prices paid m/m", "PPCDFSA066MSFRBPHI", "mom_diff"),
        ("Empire prices paid (level)", "PPCDISA066MSFRBNY", "level"),
        ("Empire prices paid 3m chg", "PPCDISA066MSFRBNY", "diff@3"),
        ("Empire prices paid m/m", "PPCDISA066MSFRBNY", "mom_diff"),
    ],
    "growth": [
        ("ISM mfg PMI (level)", "ISM_MFG_PMI", "level"),
        ("ISM mfg PMI 3m chg", "ISM_MFG_PMI", "diff@3"),
        ("ISM mfg PMI m/m", "ISM_MFG_PMI", "mom_diff"),
        ("ISM svc activity (level)", "ISM_SVC_ACTIVITY", "level"),
        ("ISM svc activity 3m chg", "ISM_SVC_ACTIVITY", "diff@3"),
        ("Baltic Dry 13w %", "BDI", "pct@13"),
        ("Philly activity (level)", "GACDFSA066MSFRBPHI", "level"),
        ("Philly activity 3m chg", "GACDFSA066MSFRBPHI", "diff@3"),
        ("Philly future activity (level)", "GAFDFSA066MSFRBPHI", "level"),
        ("Empire conditions (level)", "GACDISA066MSFRBNY", "level"),
        ("Empire conditions 3m chg", "GACDISA066MSFRBNY", "diff@3"),
        ("Empire new orders (level)", "NOCDISA066MSFRBNY", "level"),
        ("Dallas activity (level)", "BACTSAMFRBDAL", "level"),
        ("Dallas activity 3m chg", "BACTSAMFRBDAL", "diff@3"),
        ("Copper 30d %", "COPPER", "pct"), ("XLY/XLP 30d %", ("XLY", "XLP"), "ratio_pct"),
        ("SPY 30d %", "SPY", "pct"), ("KRE/SPY 30d %", ("KRE", "SPY"), "ratio_pct"),
        ("2s10s 30d chg", "T10Y2Y", "diff"), ("10y-3m (level)", ("US10Y", "US03MY"), "spread"),
        ("Claims 4w %", "ICSA", "pct@4"), ("Claims 13w %", "ICSA", "pct@13"), ("Claims (level)", "ICSA", "level"),
        ("Cont. claims 13w %", "CCSA", "pct@13"), ("Payrolls m/m (k)", "PAYEMS", "mom_diff"),
        ("Payrolls 3m chg (k)", "PAYEMS", "diff@3"), ("Unemployment 3m chg", "UNRATE", "diff@3"),
        ("Job openings 3m %", "JTSJOL", "pct@3"), ("Ind. production 3m %", "INDPRO", "pct@3"),
        ("Ind. production y/y %", "INDPRO", "yoy_pct"), ("Capacity util m/m", "TCU", "mom_diff"),
        ("Permits 3m %", "PERMIT", "pct@3"), ("Starts 3m %", "HOUST", "pct@3"),
        ("Retail sales 3m %", "RSXFS", "pct@3"), ("Retail sales y/y %", "RSXFS", "yoy_pct"),
        ("Core capex orders 3m %", "NEWORDER", "pct@3"), ("Durables 3m %", "DGORDER", "pct@3"),
        ("Mfg hours m/m", "AWHMAN", "mom_diff"), ("Vehicle sales 3m %", "TOTALSA", "pct@3"),
        ("Sentiment (level)", "UMCSENT", "level"), ("Sentiment 3m chg", "UMCSENT", "diff@3"),
        ("CFNAI (level)", "CFNAI", "level"), ("Leading index (level)", "USSLIND", "level"),
        ("GDPNow (level)", "GDPNOW", "level"),
    ],
    "liquidity": [
        ("3m - target (DFEDTARU)", ("US03MY", "DFEDTARU"), "spread"),
        ("2y - target (DFEDTARU)", ("US02Y", "DFEDTARU"), "spread"),
        ("3m - EFFR (DFF)", ("US03MY", "DFF"), "spread"), ("2y - EFFR (DFF)", ("US02Y", "DFF"), "spread"),
        ("3m - EFFR (FEDFUNDS m)", ("US03MY", "FEDFUNDS"), "spread"),
        ("2y - EFFR (FEDFUNDS m)", ("US02Y", "FEDFUNDS"), "spread"),
        ("3m bill 30d chg", "US03MY", "diff"), ("2y yield 30d chg", "US02Y", "diff"),
        ("Unemployment m/m chg", "UNRATE", "mom_diff"), ("Unemployment 3m chg", "UNRATE", "diff@3"),
        ("Challenger cuts (k)", "CHALLENGER", "level_k"), ("Claims 13w %", "ICSA", "pct@13"),
        ("Payrolls 3m chg (k)", "PAYEMS", "diff@3"), ("CPI y/y %", "CPIAUCSL", "yoy_pct"),
        ("Core PCE y/y %", "PCEPILFE", "yoy_pct"), ("Dollar 30d %", "DTWEXBGS", "pct"),
    ],
}


def load_fred(sid: str) -> ad._Series:
    f = CACHE / f"{sid}.json"
    if f.exists() and (dt.datetime.now().timestamp() - f.stat().st_mtime) < 86400:
        rows = json.loads(f.read_text())
    else:
        df = macro_data._fred_get(sid)
        rows = [(r["date"].strftime("%Y-%m-%d"), float(r["value"])) for _, r in df.iterrows()]
        f.write_text(json.dumps(rows))
    rows.sort()
    return ad._Series([d for d, _ in rows], [v for _, v in rows])


def load_any(sym: str, cache: dict) -> ad._Series:
    if sym in cache:
        return cache[sym]
    if sym in FRED:
        s = load_fred(sym)
    else:
        d, c = ad._load_close(sym)
        s = ad._Series(d, c)
    cache[sym] = s
    print(f"  {sym:22s} {len(s.dates):6d} rows  {s.dates[0] if s.dates else '-'} -> {s.dates[-1] if s.dates else '-'}", flush=True)
    return s


def pct(p):
    return "—" if p is None else f"{round(p * 100):d}%"


def main(axes: list[str]) -> None:
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    hist = {m: md._load_model(f"{m}_US") for m in ("compass", "grid")}
    events = []
    current = {}
    for m, rows in hist.items():
        ev, _ = md._events_and_dwell(m, rows, today)
        events.extend(ev)
        current[m] = rows[-1][1]

    cache: dict = {}
    report = [f"# Candidate driver sweep — {today}", "",
              "Same event study as the live panel (release-cadence windows, terciles within state, "
              "P(flip | tercile)). **Spread** = max − min tercile rate (terciles with n ≥ 8). "
              "**Lift** = P(flip | today's tercile) ÷ base. Nothing here is onboarded; it is a shortlist "
              "to review against the factor framework.", ""]

    for axis in axes:
        specs = CANDIDATES[axis]
        model = cal.MODEL_OF[axis]
        print(f"== {axis} ({len(specs)} candidates)", flush=True)
        for _, sym, _ in specs:
            for s in (sym if isinstance(sym, tuple) else (sym,)):
                load_any(s, cache)
        series = {spec[0]: ad._build_driver_series(spec, cache) for spec in specs}
        stats = ad._axis_stats(axis, model, hist[model], events, specs, series, today)
        cur_state = cal.Q_TO_AXES[current[model]][cal.SLOT_OF[axis]]
        word = {"liquidity": ("cut", "hike"), "credit": ("loosen", "tighten"),
                "growth": ("turn up", "turn down"), "inflation": ("turn up", "cool")}[axis]

        report.append(f"## {axis.upper()} — {stats['n_windows']} windows of {stats['cadence_days']}d, "
                      f"{stats['window_range'][0]} → {stats['window_range'][1]}; today state={cur_state}")
        for s in ("0", "1"):
            fs = stats["from_state"][s]
            q = word[int(s)]
            tag = "  ← today" if int(s) == cur_state else ""
            report += ["", f"### from state {s}: does it {q}?  n={fs['n_windows']}, base {pct(fs['base_rate'])}{tag}", "",
                       "| driver | n | low | mid | high | spread | today | sits | lift |", "|---|---|---|---|---|---|---|---|---|"]
            rows = []
            for d in fs["drivers"]:
                if d.get("insufficient"):
                    rows.append((-1, f"| {d['name']} | {d['n']} | not enough windows | | | | {d.get('current_value')} | | |"))
                    continue
                if d.get("categorical"):
                    continue
                ps = [p for p, n in zip(d["p_by_tercile"], d["n_by_tercile"]) if p is not None and n >= ad.MIN_TERCILE_N]
                spread = (max(ps) - min(ps)) if len(ps) >= 2 else 0.0
                lift = (d["p_current"] / d["base_rate"]) if d.get("p_current") is not None and d["base_rate"] else None
                sits = {0: "low", 1: "mid", 2: "high", None: "—"}[d.get("current_tercile")]
                rows.append((spread, f"| {d['name']} | {d['n']} | {pct(d['p_by_tercile'][0])} | {pct(d['p_by_tercile'][1])} | "
                                     f"{pct(d['p_by_tercile'][2])} | **{round(spread*100)}pp** | {d['current_value']} | {sits} | "
                                     f"{'—' if lift is None else f'{lift:.2f}×'} |"))
            rows.sort(key=lambda r: -r[0])
            report += [r for _, r in rows]
        report.append("")
        OUT.write_text("\n".join(report))
        print(f"  wrote {OUT}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or ["inflation", "growth", "liquidity"])

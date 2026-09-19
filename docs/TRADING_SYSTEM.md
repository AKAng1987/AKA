# Trading system — factor definitions

One section per factor, in the user's own framing, with the build contract
underneath. Sections are added as each factor is reviewed. The MARKOV tab's
"What moves each axis" panel is the live test of each STATE factor's drivers
against the full regime history.

---

## LIQUIDITY (monetary) — Compass axis, set by the FOMC

**Framing (user, 2026-09-17).** The central bank sets rates on its dual
mandate: price stability and employment — both belong on the driver list.
Rate hike/cut expectations follow the market's front end: the **3-month
bill** and the **2-year**. Empirically, the 3m bill's move has to be
followed by the Fed within that 3-month window; the 2y is the same vote over
a longer horizon. These sit *above* the 10-year, which is a growth /
credit-lending read, not a liquidity read. For employment: the unemployment
rate's change last-to-current, plus Challenger job cuts (rate of change, and
a level above 150k as a potential problem / intervention point). Not yet
covered: the BOJ and USDJPY carry-trade implications, the ECB and other
currencies. Simplest version: follow the 3m and the 2y — close enough to the
FedWatch tool.

**Contract (built 2026-09-17).**
- inputs: `US03MY`, `US02Y` (us_treasury, daily, 1990→); `DFEDTARU` (fred,
  daily, 2008-12→); `UNRATE` (fred, monthly, 1948→, onboarded today);
  `CPIAUCSL` (fred, monthly, 1947→).
- computation, at each 45-day FOMC window start:
  `3m − target` (level), `2y − target` (level), `3m 30d change`,
  `2y 30d change`, `unemployment m/m change`, `CPI y/y %`.
- buckets: terciles of each driver's own history within the current axis
  state; P(flip | tercile).
- granularity: per-market (one Liquidity axis).
- use: the Market column for the next FOMC on the MARKOV tab when fed
  funds futures aren't the better read (they are, for the Fed itself —
  this table is the *why*, futures are the *what*).
- log: daily row already carries the pre-registered p_flip; the driver
  readings are recomputed and cached daily (`Cache/macro/axis_drivers.json`).
- test (2026-09-16 hike): 3m − target +0.39 and 2y − target +0.99 both in
  the top tercile → 22% flip rate vs 0–2% below target, base 8%;
  conditioned 14% before the decision.
- result: adopted. 3m/2y spreads are the strongest single drivers on any
  axis so far. Unemployment separates as expected (Fed moves when
  unemployment is flat, mid tercile 26%). CPI y/y weak at this
  granularity. USDJPY tested and dropped (no separation for the US
  decision).
- open: Challenger job cuts — no free series; candidate for a manual
  monthly entry. Foreign rates (JP10Y/DE10Y/EU10Y) are in the HUD groups
  with no provider — onboarding needed before a BOJ/ECB axis. The
  tightening state has too few windows since 2008-12 to score; extending
  history via `FEDFUNDS` (1954→) would fix that at the cost of using the
  effective rate instead of the target.

### Addendum 2026-09-17 — Challenger job cuts, full history

Source resolved: TradingView's official MCP exposes `ECONOMICS:USJC`
(392 months, 1994-01→). Chart export is provider-locked but the
economic-data endpoint is not. Every value cross-checks against the firm's
own report archive (`scripts/scrape_challenger.py`, kept as the audit).
Loaded as `CHALLENGER` (source `challenger_gray`, 1M); no free API, so
the monthly refresh is an MCP pull, not a Lambda.

Tested on the FOMC windows, both states:
- **Easing (hike?)**: low Challenger → 14% flip vs 2% when high. m/m
  rate of change: no separation. The Fed hikes into labor strength.
- **Tightening (cut?)**: rising unemployment 38%, low CPI 53%, falling
  front end 27–33% — all mandate-consistent. Challenger *low* → 53%,
  *high* → 6%: the Fed cuts before the layoff count spikes (2001, 2008,
  2020 all show the first cut months ahead of the 150K crossing).
- **Conclusion on the 150K rule**: a severity marker for a regime
  already flipped, not a trigger for the next flip. Keep it on the panel
  as context; use rising UNRATE as the leading employment read, with
  Challenger as a nowcast for next month's UNRATE (to build).

### Addendum 2026-09-18 — tightening state scored; effective rate replaces target

The 2026-09-16 hike landed (compass C2→C3 at the 00:25 UTC run on the
18th, ~32h after the decision — the DFEDTARU effective-date lag). The
panel now asks "does it cut?" for the first time, which exposed the
open item above: DFEDTARU starts 2008-12, so the tightening state had
20 windows. Tested `DFF` (effective fed funds, daily, 1954→) as the
anchor: hike-side tercile rates are identical (0 / 0 / 24%) and the cut
side gets 46 windows — but the effective rate sits ~12bp under the
ceiling and FRED posts it a day late, so the "now" reading didn't match
the target spread the user thinks in. **Kept DFEDTARU** (user's call);
DFF stays onboarded for sweeps. The cut-side table below is the DFF run.

Cut-side reads (state 0, base 24%, `scripts/sweep_axis_candidates.py`):

| driver | low / mid / high → cut? | reading |
|---|---|---|
| Core PCE y/y | **60** / 0 / 12 | Fed cuts when inflation is low — mandate-consistent |
| Challenger (k) | **53** / 13 / 6 | cuts arrive *before* the layoff spike (same as 09-17 finding) |
| CPI y/y | **53** / 7 / 12 | |
| Payrolls 3m chg | 20 / **47** / 6 | strong payrolls → no cut |
| Unemployment m/m | 10 / 8 / **38** | rising unemployment → cut |
| 3m − EFFR | 20 / 14 / **35** | bill *above* funds while tight → cut?? — small n, read with care |

Today: core PCE 3.34% (high, 12%), CPI 3.33% (mid, 7%), Challenger 53k
(low, 53%), unemployment flat (high bucket, 38%). Conditioned read is
mixed; the inflation side says no cut, the labor side says the pattern
that precedes cuts is present. Candidate to add: core PCE y/y (60pp
spread, the widest on the axis). Dollar 30d % tested: 46/46/9, only
separates at the strong-dollar extreme — park under Positioning.

Also fixed in the user's Pine "Compass" script: anchor test compared
against bar *open* (`time`), which on 24h symbols (DXY, FX) opens the
prior evening UTC, so a midnight timestamp landed one bar late. Compare
against `time_close` instead; use the decision date (2026-09-16), not
the model's lagged flip date.

TradingView MCP also exposes economic series for other countries
(`get_economic_symbols` with country=JP/EU/CN), the economic calendar,
OHLCV, and the screener — the BOJ/ECB inputs and breadth columns for
later factors are one call away.

---

## CREDIT — Compass axis, set by SLOOS

**Framing (user, 2026-09-17).** Bank lending is the curve: banks borrow
short and lend long, so 10y−2y is the base line for lending margin (the
academic form is 10y−3m, since deposit costs track the policy rate; both
tested, 10y−3m is cleaner). The user also reads 10y−3m steepening against
credit default swaps — when it steepens, CDS rise. Bank stocks: KBE/SPY on
the chart, with Compass labels; going C1→C2 has been bullish (Aug 2025,
the Aug 2024 carry unwind) and C4 in 2022–23 was "a layup to volatility."
Where else to base the read was open — candidates tested below.

**Contract (built 2026-09-17).** 91-day SLOOS windows, 47 easing-state /
42 tight-state windows since 2006.
- inputs: `US10Y`, `US03MY` (us_treasury, daily); `BAA10Y` (fred, daily,
  1986→); `KBE`, `SPY` (marketstack); `NFCICREDIT` (fred, weekly, 1971→,
  onboarded today); `BUSLOANS` (fred, weekly H.8, 1947→, onboarded today).
- drivers and what they said:

  | driver | easing → tighten? lo/mid/hi | tight → loosen? lo/mid/hi |
  |---|---|---|
  | 10y−3m level | **53** / 25 / 19 — flat curve → tighten | 50 / 29 / 36 |
  | Baa−10y 30d chg | 14 / 29 / **50** — widening → tighten | **57** / 29 / 29 — narrowing → loosen |
  | KBE/SPY 30d | **50** / 27 / 20 — banks lagging → tighten | 38 / 54 / 23 |
  | NFCI credit 13w chg | 27 / 6 / **62** — tightening → tighten | **71** / 29 / 14 — easing → loosen |
  | C&I loans 13w % | 20 / 19 / **56** — post-boom → tighten | 50 / 43 / 21 |
  | SPY 30d | **67** / 19 / 12 — drawdown → tighten | 29 / 64 / 21 |

  Base rates 32% / 38%. Dropped after testing: 2s10s (level and change),
  Baa−10y level, KRE (identical to KBE), XLF/SPY (Berkshire/JPM/V/MA
  dominate — not lending-sensitive), NFCI level.
- bank earnings: no daily series; the curve level is the standing proxy.
  Bigdata.com's earnings-call tools are the candidate source if a
  quarterly "loan-loss provisions / NIM commentary" read is wanted later.
- use: Market column for the next SLOOS on the MARKOV tab.
- today (2026-09-17): split — KBE −4% (50%), SPY −3% (67%), loans +10%
  (56%) say tightening; curve mid, spreads narrowing (14%), NFCI flat
  (6%) say no. Conditioned 36% vs 32% base. Price is worried, credit
  markets aren't.

**Curve language (agreed to add, 2026-09-17).** "Steepening" alone mixes
two opposite worlds; the names say which end moved:

| | short end | long end | bonds | typical cause |
|---|---|---|---|---|
| bull steepener | falls fast | flat/down a little | up | Fed cutting into weakness |
| bear steepener | flat | rises fast | down | term premium, inflation/supply, fiscal |
| bull flattener | flat | falls fast | up | flight to quality, growth scare |
| bear flattener | rises fast | flat | down | Fed hiking (2022) |

The user's CDS observation is the **bull** steepener: the curve steepens
because the front end collapses as the Fed cuts into a downturn, which is
when default risk reprices. H2 2023 bear-steepened just as much with CDS
tight. Encoding: Δ(10y−3m) over 30d for magnitude, Δ(3m) over 30d for
which end, and a derived four-way label tested like any other driver.
To build next on this axis.

**Also noted.** Challenger: 80–100K m/m spike = volatility, 150K =
crisis — thresholds for the consequence/volatility side, not flip
predictors (see Liquidity addendum).

---

## How to read the panel's probabilities (2026-09-19)

Every number on "What moves each axis" is a count, not a model.

- **Base rate** — cut history into release-cadence windows (CPI/GDP 30d,
  FOMC 45d, SLOOS 91d). Keep the windows where the axis was in today's
  state. Base = windows that ended with a flip ÷ all such windows.
  Credit easing: 15 of 47 → 32%.
- **Driver row** — the same count on a subset. Split the driver's own
  readings into thirds (terciles); "10y−2y · low · 53%" = of the easing
  windows where 10y−2y was in its bottom third, 53% flipped. A third
  needs ≥ 8 windows before its rate is shown.
- **Today** — the plain average of each driver's rate at its current
  tercile. No weights, no fitting.

Why it never says 90%: with ~47 windows a third is "x of ~15", and an
axis that flips one time in three on average cannot be pushed to nine in
ten by one indicator. When drivers disagree the average pulls back
toward base — the user's "everything converges to ~37%" observation is
this averaging. A read far from base only appears when the drivers
*agree*; read the panel by counting gold rows, not by the headline.

Not covered: *when* the flip comes (that is the Markov P(flip) at the
next release) and how long the regime lasts. The BACKTEST leaderboard
answers "what works while it lasts"; timeframe drift is real and shows
up as position churn.

Context filed (user's spreadsheets, 2026-09-19): SPX drawdown base
rates 1946–2024 (5–10% ~95%/yr, 10–15% ~33%, 15–20% ~15%, 20–35% ~14%,
≥35% ~5–7%) — the *consequence* table to pair with a regime, e.g.
"P(≥10% correction | C3G3)"; queued for BACKTEST. Fiscal acts table
(2001–2025, sector ETFs, 10y at enactment, party, size) — the POLICY
factor's input, an event study on the affected ETFs after enactment.
1970s vs 2020s comparison — narrative layer, not a number.

---

## INFLATION — Grid axis, set by CPI

**Framing (user, 2026-09-19).** Watch ISM services and manufacturing
prices paid, USCI, DBC, DBA, USOIL, copper, CPI (the model's own
measure), then whichever gauge the Fed is leaning on (core PCE / core
CPI), and on the bond side TIPS less nominal (the breakeven). Not yet
used but should be: the dollar's effect on net imports/exports → prices;
shipping prices (also useful for a logistics tab). Live prices should
enter as a rolling ~30-day move matched to the release frequency — "as
oil goes up, the probability that CPI rises." Ideally the panel shows
which input is pushing the probability up or down.

**Contract (built 2026-09-19).** 30-day CPI windows, 107 cool-state /
117 hot-state windows since 2008-03. Sweep of 28 candidates in
`docs/CANDIDATE_SWEEP_2026-09-18.md`.

| driver | cool → turn up? lo/mid/hi | hot → cool? lo/mid/hi |
|---|---|---|
| WTI 3m % | 20 / 25 / **53** | **46** / 26 / 18 — oil falling → cools |
| WTI 30d % | 9 / 33 / **56** | 41 / 26 / 23 |
| DBC 30d % | 11 / 39 / **47** | **44** / 28 / 18 |
| PPI commodities 3m % | 17 / 36 / 44 | 38 / 36 / **15** — pipeline cooling → cools |
| Import prices 3m % | 17 / 36 / 44 | 36 / 38 / **15** |
| Dollar 30d % | 31 / 42 / 25 | 20 / 28 / **41** — strong dollar → cools |
| 5y breakeven 30d chg | 14 / 33 / **50** | **44** / 26 / 20 |
| CPI m/m % (last print) | 11 / 25 / **61** | **64** / 18 / 8 — near-circular, "what the print said" |
| Core PCE y/y % | 46 / 28 / 25 | 28 / 36 / 26 — context only |

Base rates 33% / 30%. Dropped after testing: DBA (13pp), copper (10pp),
hourly earnings, Michigan 1y expectations, 10y breakeven level, sticky
CPI, shelter CPI.

**Addendum, same day — TradingView pulls tested** (`docs/CANDIDATE_SWEEP_2026-09-19.md`):

| driver | cool → turn up? | hot → cool? |
|---|---|---|
| ISM svc prices m/m | 23 / 22 / **56** | **47** / 22 / 20 — added |
| ISM mfg prices (level) | 25 / 28 / **47** | 36 / 36 / **18** — added |
| USCI 30d % | 20 / 43 / 40 | **43** / 29 / 14 — added (from 2010) |
| ISM mfg prices m/m, 3m chg | 38pp / 27pp | 5pp / 12pp — one-sided, skipped |
| Baltic Dry 4w / 13w % | 19 / 5pp | 5 / 26pp — inconsistent, parked for logistics tab |

Sources: `ECONOMICS:USMPR`, `ECONOMICS:USNMPR` (monthly, loaded as
`ISM_MFG_PRICES` / `ISM_SVC_PRICES`, source `tradingview`), `INDEX:BDI`
weekly closes as `BDI`. TradingView has no headline services PMI in its
economics catalog; refresh is a monthly MCP pull, same as Challenger.

- inputs onboarded today: `DCOILWTICO`, `DTWEXBGS`, `PPIACO`, `IR`,
  `PCEPILFE` (all fred).
- today (2026-09-19, hot state): oil +27% 30d / +26% 3m and DBC +8%
  all in the top third (18–23% cool vs 30% base) — price says no
  cooling; PPI commodities −0.8% and import prices flat in the bottom
  third (36–38%) — pipeline says cooling is on the table. Dollar −0.8%
  low (20%). Split: oil hot, pipeline soft, dollar weak.

---

## GROWTH — Grid axis, set by GDP

**Framing (user, 2026-09-19).** ISM services and manufacturing, GDPNow.
Open to other series if they test well. Fiscal spending programs
(CHIPS, IRA, IIJA) are "a year's worth of growth as cash comes in" —
narrative that needs its own backtest (see POLICY, queued).

**Contract (built 2026-09-19).** 30-day GDP windows, 121 down-state /
103 up-state windows since 2008-03. Sweep of 30 candidates.

| driver | down → turn up? lo/mid/hi | up → turn down? lo/mid/hi |
|---|---|---|
| GDPNow (level) | 23 / 41 / **56** — high nowcast → turn up | 54 / 30 / 45 |
| Durables 3m % | 35 / 28 / **54** | — |
| Core capex orders 3m % | 28 / 40 / **49** | — |
| Claims 13w % | 45 / 48 / **24** — falling claims → turn up | — |
| CFNAI (level) | 25 / 45 / 46 | — |
| Retail sales y/y % | 28 / 42 / 46 | — |
| Ind. production 3m % | 30 / 38 / 49 | — |

Base rates 39% / 43%. Nothing previously on the axis separated: copper
(22pp but inverted), XLY/XLP, SPY, KRE/SPY, 2s10s — all dropped.
Conference Board leading index (USSLIND) discontinued on FRED 2020 —
ignore. GDPNow enters as the FRED vintage series (quarterly rows) for
history; the live nowcast is on the MACRO tab.

**Addendum, same day — ISM tested** (`ECONOMICS:USBCOI` = ISM mfg PMI,
`ECONOMICS:USNMBA` = ISM services business activity; no headline
services PMI in the catalog):

| driver | down → turn up? | up → turn down? |
|---|---|---|
| ISM mfg PMI 3m chg | 18 / **54** / 44 — top driver on the axis | **53** / 35 / 40 — added |
| ISM mfg PMI (level) | 28 / 42 / 46 | 38 / 50 / 41 — added |
| ISM svc activity 3m chg | **44** / 48 / 24 — *inverted*: rising activity → less turn-up | 7pp — left off |
| ISM svc activity (level) | 12pp | 18pp — left off |
| Baltic Dry 13w % | 17pp | 16pp — marginal, parked |

- inputs onboarded today: `GDPNOW`, `DGORDER`, `NEWORDER`, `ICSA`,
  `CFNAI`, `RSXFS`, `INDPRO` (all fred).
- today (2026-09-19, down state): GDPNow 5.1 high (56%), core capex
  +3.5% high (49%), claims −14% low (45%), durables −2.4% low (35%).
  Leans turn-up vs 39% base.

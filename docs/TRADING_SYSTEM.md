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

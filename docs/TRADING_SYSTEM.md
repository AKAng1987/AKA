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

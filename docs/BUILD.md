# CGI — Compass Grid Identifier

**Full build write-up, 2026-09-25.**

This is the umbrella document. It says what CGI is, what each of the seven
factors does, what has actually been measured, and — at equal length — what
is not known. Per-factor detail lives in `TRADING_SYSTEM.md`,
`BREADTH_STUDY.md`, `FUNDAMENTALS_PLAN.md` and the `CANDIDATE_SWEEP_*.md`
files; this document is the map over them.

Written to be read cold, in a year, by the person who built it.

---

## 1. What the tool is for

The problem CGI exists to solve is not "what is the market doing". It is
**"it can't be that I keep flipping"** — a regime model on a weeks-to-months
clock, read every morning, asks you to re-decide a position every morning.
That is how conviction gets traded away.

So CGI is built as **layers on different clocks**, slowest first:

| Layer | Clock | Who decides | Where |
|---|---|---|---|
| Standing themes | ~1 year | **you**, monthly | LIVE, top |
| Rotating themes | weeks–months | detected from price | LIVE, table |
| Who earns it | quarterly | fundamentals | FUNDAMENTALS |
| Edge ranking | regime-conditioned | backtest | LIVE strip / BACKTEST |
| Tactical | days | breadth + your eyes | LIVE strip / TAPE |

The daily scan was **demoted** out of the front page into TAPE for exactly
this reason. LIVE answers "what am I holding and why"; TAPE answers "what
moved last night".

### The regime model underneath

Two 2×2s, giving 16 states:

- **Compass** = Liquidity × Credit → C1–C4
- **Grid** = Growth × Inflation → G1–G4

`Q_TO_AXES = {1:(1,0), 2:(1,1), 3:(0,1), 4:(0,0)}` — quadrant to
(first axis up, second axis up), same table for both.

Today: **C3G3**. Compass flipped C2→C3 on 2026-09-17 (FOMC hike, liquidity
tightening, credit still accelerating). Grid moved to G3 on 2026-09-11.
Compass history runs from 2004-07-01, Grid from 2008-03-27.

Each axis moves on exactly one release, which is the whole basis of the
Markov layer: FOMC→liquidity, SLOOS→credit, CPI→inflation, GDP→growth.

---

## 2. The seven factors

Numbered as you framed them. The honest state of each is at the end of its
section.

### 2.1 LIQUIDITY — monetary policy
*Compass axis, set by the FOMC. `axis_drivers.py`, `DRIVERS["liquidity"]`.*

The framing you gave: the Fed sets rates on its dual mandate; **the front
end of the curve is the market's vote on the next move**. The 3-month bill
has to be followed within the meeting window, the 2-year over a longer one.
The 10-year is growth and credit, not liquidity — it is deliberately absent.

Drivers: 3m bill − Fed target, 2y − Fed target, 3m bill 30d change, 2y yield
30d change, unemployment m/m, Challenger cuts (level, thousands), CPI y/y.

Two decisions worth remembering:

- **DFEDTARU, not DFF.** The effective rate has history back to 1954 and
  scores the tightening state on more windows, but it sits ~12bp under the
  ceiling and posts a day late, so the "now" reading did not match how you
  think about the spread. You looked at it and said *"dff doesnt look
  right"*. DFEDTARU stands; DFF stays onboarded for sweeps only.
- **Challenger job cuts: the Fed cuts before the layoff spike.** In the
  tightening state, LOW Challenger → 53% chance of a cut; HIGH → 6%. 2001,
  2008 and 2020 all show the first cut months ahead of the 150k crossing.
  **150k is a severity marker, not a trigger** — the most counter-intuitive
  result in the build, and it inverts the obvious reading.

*State: complete and tested. This is the best-understood axis.*

### 2.2 POLICY — fiscal and geopolitics
*`notes_data.py` (POLICY register), `policy_watch.py`, NOTES page.*

Your prior: **policy takes about a year to reach the system.** That is why
standing themes are held ~12 months and why this factor is a register rather
than a signal.

The register works *forward*: a policy lands → it names the themes it should
move → those themes are checked against relative strength. A policy note
shows `is_live` while any theme it points at is still running on RS. The
point is to be early, not to explain a rally six months late.

Eight entries so far, each with country, type (monetary/fiscal/trade/
geopolitical), announcement date (**announcement, not effective — markets
move on announcement**), themes, tickers, source, confidence. Notable:

- **JP 2025-10-01, Takaichi.** Japan +13.8% in yen, USDJPY +8.1%, EWJ only
  +5.3% — the instrument mattered as much as the direction. This one finding
  changed the Japan proxy from EWJ to DXJ.
- **US 2025-04-02, reciprocal tariffs.** CRAK started running a month later
  and is now 506 days old, a megatrend.
- **CHIPS (2022-08-09) and the IRA (2022-08-16) are seven days apart**, so
  they cannot be separated in an event study. That is a structural limit of
  the factor, not a data gap.
- **CARES (2020-03-27)** lands on the COVID bottom, so any measured "policy
  effect" is really the rebound.

`policy_watch.py` reads central bank RSS directly — Fed, ECB, BOJ, BoE —
filters for policy-shaped items and returns **candidates**. Nothing is
promoted automatically, because deciding an announcement matters is
judgement. What has no usable feed is recorded on the page rather than
quietly omitted: BSP blocks automated requests (403), Bank of Korea and the
PBoC publish HTML only. **Korea, the Philippines and China stay manual.**

*State: the register is live and the watcher works for four central banks.
Your fiscal-acts file is not yet fully backtested into it. Korea/PH/CN have
no automated coverage.*

### 2.3 INFLATION — Grid axis, set by CPI
*`DRIVERS["inflation"]`.*

Your framework was ISM services prices, ISM manufacturing prices, USCI, DBC,
DBA, USOIL, copper, CPI. The sweep then told us which of those actually
separate flip windows from non-flip windows, and the answer was not the whole
list: **DBA, copper, hourly earnings and Michigan expectations did not
separate and were dropped.**

Final: Empire prices paid (m/m and 3m), ISM services prices m/m, ISM
manufacturing prices level, WTI (3m and 30d), DBC, USCI, PPI commodities 3m,
import prices 3m, dollar 30d, 5y breakeven, CPI m/m, core PCE y/y.

Two things forced themselves in:

- **TradingView's ECONOMICS group broke.** You bought the add-on, reconnected,
  and the MCP still returns `permission denied group economics_paid` while
  charts work fine — a TradingView-side bug, raised with them. So the ISM
  rows are **frozen at their loaded history**. The replacement is free FRED
  regional Fed surveys: **Empire State prices paid tested at the same 26pp
  spread as ISM services prices (27pp) and prints the 15th, two weeks
  earlier.** ISM stays in the panel for context and resumes if they fix it.
- **Calendar-aware year-over-year.** Row-counting broke on the shutdown gap
  (BLS skipped Oct-2025 CPI; FRED has no row), which put CPI y/y at 3.73%
  instead of 3.33%. `yoy_pct` now finds the observation on or before the same
  date a year earlier.

*State: complete, tested, and the numbers now back what you were already
saying. ISM is on a frozen feed pending TradingView.*

### 2.4 GROWTH — Grid axis, set by GDP
*`DRIVERS["growth"]`.*

Your framework was ISM services and manufacturing plus GDPNow, with the
addition that **which sectors are short of supply is the important feature** —
that is the bridge from this factor into themes, and it is why copper,
memory and energy shortage show up as themes rather than as growth drivers.

Final: Philly Fed future activity, ISM mfg PMI (3m change and level), ISM
services activity, GDPNow, durables 3m, core capex orders 3m, claims 13w,
CFNAI, retail sales y/y, industrial production 3m. Dropped by the sweep:
copper, XLY/XLP, SPY, KRE/SPY, 2s10s.

- **ISM mfg PMI 3-month change is the top driver.**
- **ISM services activity reads inverted** and is kept as context: high
  services activity has gone with *fewer* growth turn-ups (40/44/32 across
  terciles). Do not read it the obvious way.
- **Philly Fed future activity** (free, 1968→, keeps updating) tested at 22pp
  and is the live read while ISM is frozen.

*State: complete and tested. Supply-shortage-by-sector is expressed through
themes, not yet as its own measured driver.*

### 2.5 POSITIONING — COT
*`cot_data.py`, POSITIONING page.*

Free, direct from the CFTC public reporting API. 26 contracts across energy,
metals, agriculture, rates, FX and equity. Verified against your officemate's
dashboard: NAT GAS NYME large specs −221,587 on 2026-09-15 against his −222K.

The read: commercials hedge a business and are price-insensitive; **large
specs are trend followers and are most long at tops and most short at
bottoms — that is where the edge is**; small traders usually sit with the
specs. Extreme positioning does not *cause* a reversal. It removes the fuel
for continuation and makes the risk asymmetric.

The COT index (Williams) is reported on **two bases, because they disagree
and the disagreement is information**: raw net contracts, and net as a share
of open interest. Natural gas in Sept 2026 was 12th percentile on raw
contracts but 34th on share of OI — open interest has grown enormously, so
raw percentiles flatter recent extremes. Extremes flag at <10 / >90.

Carried limits, deliberately: the data is **Tuesday's close published Friday
15:30 ET** — three days stale on arrival, up to ten before the next one. And
**extremes persist**: 2011 sat pinned at extreme short for months while price
kept falling. This is a condition, not a trigger; price has to stop
confirming first.

Contract names had to be stitched — NYMEX natural gas was "NATURAL GAS" until
Feb 2022 and "NAT GAS NYME" after; crude is "WTI-PHYSICAL".

**The mystery worth recording:** your officemate's indicator printed 10,593 /
−12,234 / +1,641 for what it labelled natural gas. Those are an exact match
for the **US Dollar Index** contract. His indicator is mapped to the wrong
contract.

*State: built and verified. Your own note stands — this needs more, because
you trade equities and the COT is a futures report. See §5.*

### 2.6 TECHNICALS — breadth and trends
*`technicals_data.py`, `themes_data.py`, `BREADTH_STUDY.md`.*

Your definition, which is the one implemented: **breadth and trends. Technical
analysis itself stays your eyes.** CGI does not read chart patterns.

#### Breadth

Net new highs on the **Nasdaq Composite** (HIGQ − LOWQ), matching your "Net
Highs/Lows v6" indicator — verified by reproducing the exact value on your
chart, 37 − 91 = −54. NYSE gives a different number, and that mismatch is
what identified the universe. Plus six participation gauges: NCFD, MMFD,
MMTW, MMFI, MMTH, NCTH.

Colour follows the indicator's own rule, not a threshold of mine: three
consecutive days of net highs is green, three of net lows is red, anything
else is white chop. **Colour and the 8/20 EMA cross are separate fields and
both are always reported** — because you asked for exactly that: a cross down
while the tape is still green is the earliest warning that chop is starting
and profits should come off; a cross up from below is a possible
rate-of-change shift, strongest when it holds *against bad news*.

What the study found, over 4,969 aligned days:

- **Breadth prices risk, not direction.** |r| ≤ 0.10 against SPY forward
  returns at 5/20/60 days. What separates is drawdown: MMTH above 70 has
  meant −1.9% over 20 days, below 30 −5.0% (60d: −3.1% vs −8.8%).
- **The same reading inverts by regime.** MMTH < 30 at 60 days: in C1, 49% up
  and −11.0% drawdown — a falling knife. In C2, 97% up and +10.9%, the best
  setup in the study (n=153, concentrated in 2009-10 and 2020, so read the
  direction not the magnitude).
- **MMTH falling through 30 is the only negative-expectancy state found**
  (n=41: 44% up over 20 days, −1.8% return, −11.2% drawdown at 60 days). And
  reclaiming 30 is still bad, so that line is not an all-clear.
- 30/70 thresholds, not medians — a median split washes an oscillator out.
  The 30/70 cut roughly triples the drawdown spread.
- **The 8/20 cross does not separate on its own** (65% vs 66% up). It is a
  state label, not a signal. This is why it is reported beside the colour
  instead of instead of it.

#### Trends — the themes engine

This is the piece you called the good idea: **divide each name by SPY, track
it, and read edge and duration off the run.**

```
RS    = ticker / SPY
trend = 200-day EMA of RS
onset = earliest day from which RS stayed above trend on >= 80% of days since
age   = days since onset
```

The 80% persistence rule matters: a strict "every day above" test restarts
the clock on every one-day dip, which is how a mature theme gets called
"new". 28 themes, each with ordered proxies so the leading leg comes first —
**miners lead the metal** (COPX turned 68 days before CPER; GDX is running
while GLD is not, and that gap is itself the signal).

Staging is **empirical**, and this is the correction you caught. The first
version shipped with invented 120/400-day cut-offs, which called a 100-day
run "emerging" when you would already have been holding it past the median.
Re-measured with the detector's own definition of a run (dips under 15 days
merged, runs under 20 days discarded): **727 completed runs, median 82 days,
mean 138, p75 182, p90 328.** Stages are terciles of that distribution
(early ≤51d, mid ≤137d, late after). Measuring raw EMA crossings instead
gives a median of 3 days and is meaningless — RS crosses its trend
constantly.

The number reported is not age but **survival: the share of historical runs
that lasted longer than this one has.** That answers the question that
matters — not how old the run is, but how much runway runs of that age have
had left.

**Megatrends are a rule, not a list**, at your instruction: anything trending
more than 365 days has stopped being a rotation. For those, survival is
reported as context rather than a countdown, because the survival curve is
measured over rotational runs and would read "0% runway" when it means "this
is the longest run in the sample".

Three standing themes are promoted today: AI capex — the $1T build-out; The
Dollar — hikes into a steepener; Energy pricing power — routes and shortage.
Each carries a thesis, tracked ETF proxies, your named-equity watchlist, and
an **explicit exit rule**.

*State: complete and the strongest-tested factor. Individual charts stay your
job, by design.*

### 2.7 FUNDAMENTALS — built 2026-09-25
*`api/fundamentals_data.py`, `api/sec_xbrl.py`, `FUNDAMENTALS_PLAN.md`.*

Damodaran's five, **all as rate of change**, plus the who-pays-whom map. The
read throughout is the **second derivative**: a company going from +30% to
+20% revenue growth is decelerating while still growing fast, and that is
what separates "the theme is working" from "this company is capturing it".

**Source is the SEC, not a vendor.** The alternatives were mapped and both
failed:

- **FMP free gates per symbol.** AMD and NVDA answered; MU and CRM were
  `ACCESS DENIED` on the identical call. It also caps quarterly history at 5
  rows and returns *sequential* quarter-on-quarter growth, which is
  seasonally contaminated — AMD's Q1 always looks flat against a big Q4 and
  would print as "decelerating" every year. ETF holdings and transcripts need
  the Ultimate plan.
- **TradingView has no server-side API**, only the MCP, so it would need a
  scheduled routine — and its scanner host returned 429 on every endpoint
  during this build while the ECONOMICS group is already broken. Two factors
  would have shared one point of failure.
- **SEC XBRL** is free, needs no key, gates nothing, covers all 10,413
  filers, carries full history (MU has 35 quarters against FMP's 5), gives
  *true* year-on-year with no chaining, and is the filed number itself.

Render needs only a User-Agent header, so the endpoint fetches live. That
removed the "no FMP key on Render" constraint the first draft was built
around. **63 of 65 filers resolve in ~15 seconds** (X is acquired; AEM files
IFRS, not us-gaap, and is excluded rather than shown stale).

Constituents hang off `themes_data.THEMES` keys and are asserted against
them, so a theme renamed there fails loudly instead of silently emptying the
table. They are hand-seeded anchors, not fund weightings, because ETF
holdings are paywalled.

Two correctness details worth keeping:

- **Q4 is derived** from the annual figure where a filer never tagged it as a
  90-day period. That recovered 5 of MU's 35 quarters; without it a quarter
  of the history silently disappears.
- **An accumulated deficit drags Altman Z'' negative regardless of
  solvency.** SNOW scored −4.7 while holding net cash; ABBV −0.01 on $12.8bn
  of FCF. The band is withheld and the reason reported, rather than printing
  "distress" about a company that is fine.

#### What it found immediately

- **The AI layer cake is not moving together** — which the thesis predicted.
  Median revenue acceleration: chips **+37.6pp**, applications +3.3pp,
  infrastructure +2.3pp, energy **−0.4pp**. Infrastructure slowing and
  utilities falling are confirmed (CEG −10.3pp, NRG −9.7pp, VRT −6.0pp,
  SMCI −29.5pp).
- **But chips contradict the thesis.** The standing note says AMD is working
  while NVDA decelerates. The filings say the opposite: **NVDA +20.6pp
  against AMD's +12.3pp**, with MU +149.4pp and AVGO +37.6pp ahead of both.
  Chips are not splitting — they are the strongest layer in the cake, and AMD
  is its weakest member.
- **Applications are buying their growth.** Four of six read "buying growth"
  — revenue accelerating, margin not expanding (NOW −6.80pp, PANW −5.66pp).
  PLTR is the only one capturing. That is precisely the real-revenue-versus-
  press-release question, answered.
- **MU is the strongest fundamental in the universe**: +345.7% YoY,
  acceleration +149.4pp, margin +46.85pp, on a sustained ramp (+36.6, +46.0,
  +56.7, +196.3, +345.7) rather than a spike. It confirms Korea/DRAM.
- **Copper's price and its fundamentals disagree.** FCX is rolling over
  (−8.1% YoY, −20.3pp) while COPX/CPER ran on RS. A theme can run on price
  while the companies inside it deteriorate — which is the reason this factor
  exists.

*State: built and live. The who-pays-whom map is seeded with 7 links and is
the half that grows by hand.*

---

## 3. Architecture

```
TradingView / FRED / CFTC / central bank RSS
        |
        v
DynamoDB (ap-southeast-1)
  cmon-stage-backend-price-history     PK symbol / SK date   263 symbols, 1,035,241 rows
  cmon-stage-backend-model-history     the regime rows, pre-registered
  cmon-stage-backend-metrics-source
  cmon-stage-backend-regime-signals    one row per day, the audit trail
        |
        v
FastAPI on Render  (cgi-api-9mim.onrender.com)   bearer token
  + S3 cache  Cache/macro/*.json   TTL_HOURS + CACHE_SCHEMA_VERSIONS
        |
        v
Next.js on Vercel  (cgi-vercel.vercel.app)   same-origin proxy routes
        |
  LIVE ◉ · TAPE ≡ · BACKTEST ⊞ · MACRO ◎ · MARKOV ⇄ · POSITIONING ⚖ · FUNDAMENTALS ⊟ · NOTES ✎
```

### Endpoints

`/api/live` `/api/macro/{rates,growth,dot-plot}`
`/api/backtest/{compass}/{grid}` (+ `/occurrences`) `/api/signals` `/api/markov`
`/api/cot` `/api/policy-watch` `/api/notes` `/api/technicals` `/api/themes`
`/api/series` (GET/POST) `/api/fundamentals` `/api/watchlists`

### The cache, and the trap in it

One S3 object per key, TTL decided by S3 `LastModified`. Current TTLs: themes
24h, technicals 12h, COT 12h, policy-watch 6h, axis_drivers 24h, hud_extra
6h, dot plot and FOMC calendar 168h.

**`CACHE_SCHEMA_VERSIONS` is the part that will bite you if you forget it.**
Bumping a key's version forces an immediate refetch regardless of remaining
TTL. It exists because a bug class kept recurring: fix the code, deploy, and
the endpoint keeps serving the old value for up to TTL more hours. It bit us
again in this build — you said *"also you didnt change the theme"*, and the
standing themes, Japan-via-DXJ and Korea/DRAM were all invisible because the
version had not been bumped. **Change the shape of what a key computes, bump
its version in the same commit.**

`get_or_fetch` runs a sanity check on 24h+ keys and on failure serves
stale-but-known-good rather than fresh-but-corrupt. `json.dumps(allow_nan=False)`
is deliberate: the default silently writes NaN tokens that round-trip and then
blow up FastAPI's own serializer.

### Routines (3)

| Routine | Schedule | Does |
|---|---|---|
| `CGI · TradingView watchlist refresh` | daily 01:00 UTC | rewrites `CGI · now` and `CGI · if next flips` |
| `CGI · monthly TradingView data pull` | 8th monthly | ISM / manual series |
| `CGI · monthly theme review` | 24th, 09:00 Manila | DUE FOR REVIEW / POSSIBLE BREAKS / CANDIDATES / AGEING |

The theme review makes **no buy or sell recommendation**. It hands you a
shortlist and you reply with promotions; the file is then edited by hand.
That split is intentional — layer 1 is the slow layer and its whole purpose
is not to move when the regime rotates.

**No subagents have ever been spawned in this build.** Themes and NOTES do
not update via an agent; they are cached endpoints recomputed server-side on
the first request after expiry. Worth knowing so you do not look for an agent
that is not there.

---

## 4. Data integrity — read this before trusting any old number

**Any backtest figure read before 2026-09-24 contained fabricated moves for
roughly 28 tickers.** What was wrong:

- **32 unadjusted splits**, 83,204 rows back-adjusted. MarketStack serves raw
  closes, so a split is a step in the series indistinguishable from a real
  return — inside the backtest that fabricates a ±60–80% move in whatever
  regime window contains the date.
- **118 zero-closes** deleted.
- **MAGS served three different securities** under one symbol. 2,032 rows
  purged, 867 reloaded from `CBOE:MAGS`, and MAGS removed from the MarketStack
  refresh.
- **UUP had only one C3G3 occurrence** because MarketStack coverage started
  2020-06. Repulled from `AMEX:UUP`: 4,930 rows back to 2007.

Two repair lessons in `scripts/fix_splits.py` worth keeping:

1. **A one-day bad print makes TWO large moves** — down, then back up — and
   the recovery looks exactly like a split. Glitch rows must be deleted and
   the series re-read *before* any split is judged, or the recovery leg gets
   back-adjusted by its own ratio. My first pass would have scaled USDRUB by
   104×. Hence the two-pass design.
2. **Some huge moves are real.** A >60% threshold catches the 2018
   volmageddon VIX spike and oil's negative print in April 2020. `GENUINE =
   {VIX, USOIL, NATGAS, UNG}` is an explicit allow-list, with UNG's real
   2024-01-24 4.19 split applied by hand via `FORCED_SPLITS`.

Verified zero remaining anomalies after the repair.

### The fundamentals build added two more, 2026-09-25

**A dead XBRL concept reads exactly like a live one.** Filers switch concepts
mid-history. NVDA used `RevenueFromContractWithCustomerExcludingAssessedTax`
until 2022 and then moved to `Revenues`; taking the first tag present read a
series that had been dead four years and reported **FY2020's $3.1bn as the
latest quarter**, with a confident and wrong verdict attached. **48 of 65
names merge more than one tag**, so most of the first run was affected, and
the first numbers written into this document were wrong.

Fixed three ways: merge the whole fallback chain rather than choosing one tag;
add `RevenuesNetOfInterestExpense` for banks (JPM's plain `Revenues` stops in
2025, WFC's in 2020); and add a **`STALE_DAYS = 200` guard** that refuses any
series whose newest quarter predates a reporting cycle. The guard is the part
that matters — it turns this whole bug class from a silent wrong answer into a
visible gap, which is how AEM surfaced as an IFRS filer instead of pretending
to be current.

**Memory, not rate limit, is the ceiling on SEC fetches.** A `companyfacts`
payload decompresses to 3–8MB and carries 350–780 tags; the universe is
~314MB, several times that again parsed into Python. Eight workers ran fine
locally and would not have survived Render's instance. Each payload is now
trimmed to the ~20 tags in use *inside* the worker and released immediately,
with workers cut to 3: peak RSS 145MB. Same mistake class as the 33-second
LIVE load in §7 — caught this time before it stuck.

---

## 5. What is not known

Kept as a list because it is the most useful section in a year's time.

- **Position sizing across asset classes is unsolved, and it is the live
  gap.** Your own words: currency pairs are often first and fastest to move,
  but the same move needs far more leverage, and you do not have a rule for
  adjusting size. Nothing in CGI sizes anything. The stated principle —
  risk-adjusted returns, sized against the holding period of the catalyst —
  is not implemented anywhere.
- **Positioning is thin for an equity trader.** The COT is a futures report.
  It says nothing about equity positioning, and that is the factor you
  flagged as needing more.
- **Hold-past-flip is unmeasured.** What happens if you hold N days past a
  regime change is the question your NATGAS review raised and the tool still
  cannot answer. The related finding is recorded: NATGAS in C2G4, avg high
  +13.2%, avg low −9.7%, avg return +1.8% over 21 occurrences — **the trade
  went the right way at some point in nearly every window, and holding to the
  regime's end gave most of it back. The edge is in the range, not the
  trend.**
- **Industry-level RS needs constituent mapping.** Your own list is two years
  stale and does not separate the oil names by midstream/downstream. A
  TradingView screener is the proposed route.
- **Cross-asset / currency** is agreed to live in NOTES rather than as its own
  factor, but the rolling-correlation work behind it is not done.
- **El Niño is a candidate, untested.** It propagates through agriculture the
  way fiscal policy does, and unlike a trade war it is measurable and
  repeatable — NOAA's Oceanic Niño Index is free and monthly back to 1950.
- **ISM is on a frozen feed** pending TradingView's fix.
- **Named equities are watchlist-only, not tracked series.** Onboarding
  individual stocks is unbounded scope; the architecture is ETF-level by
  design.
- **Axis-driver windows are fixed-length and anchored to the first available
  date**, not to real historical release dates (those are not stored before
  2026). Good enough for tercile-level conditional rates; not for anything
  finer.
- **Markov flip probabilities are read from history and dwell time, not
  fitted.** The panel is a table to read, not a model to trust —
  `conditioned_p_flip` is a plain equal-weight mean of per-driver
  P(flip | current tercile), shown next to the unconditional base rate on
  purpose.

---

## 6. Standing constraints

- **Never paste secrets into Claude.ai chat** — PATs, API keys, access keys,
  tokens. Secrets go direct from Terminal to destination. Pushes of non-secret
  code via Bash are fine.
- **Do not paste CGI prompts into another Claude Code session.**
- **AWS `update-function-configuration --environment` replaces the entire env
  map.** Use the Console for environment edits.
- Cloud routines need `onrender.com` and `vercel.app` allowed in the
  environment's network access, or they fail on egress.
- `series_write.py` is append-only and whitelisted (ISM ×4, CHALLENGER, BDI),
  with range checks, future-date rejection and a 24-row cap.

---

## 7. Performance note

LIVE and TAPE once loaded in 33 seconds. The cause was my own fix: bringing
tickers absent from the legacy S3 workbook onto TAPE added ~40 sequential
DynamoDB queries. Parallelised to 12 workers and cached 6h as `hud_extra` →
**1.2–1.5s**. If either page gets slow again, look for a newly added
per-symbol loop before looking anywhere else.

---

## 8. Where to look next

Files that carry real decisions, in the order they matter:

| File | Holds |
|---|---|
| `api/axis_drivers.py` | the four axes' driver lists and why each survived |
| `api/themes_data.py` | RS detector, survival curve, the three standing themes |
| `api/technicals_data.py` | breadth glance, built to your reading order |
| `api/cot_data.py` | contracts, name stitching, the two index bases |
| `api/fundamentals_data.py` | the five as rate of change, theme constituents, who-pays-whom |
| `api/sec_xbrl.py` | SEC concept fallbacks, Q4 derivation, true YoY |
| `api/notes_data.py` | POLICY / NARRATIVE / FINDINGS registers |
| `api/cache.py` | TTLs and the version-bump discipline |
| `scripts/fix_splits.py` | the two-pass repair, and why it is two passes |
| `docs/TRADING_SYSTEM.md` | per-factor framings and contracts |
| `docs/BREADTH_STUDY.md` | the full breadth tables |
| `docs/FUNDAMENTALS_PLAN.md` | tomorrow's spec |
| `docs/CANDIDATE_SWEEP_*.md` | what was tested and rejected, by date |

---

*Seven factors, all seven built. The regime model is the skeleton,
themes are the muscle, and the layering is the point — it exists so that a
position survives contact with the next morning's data.*

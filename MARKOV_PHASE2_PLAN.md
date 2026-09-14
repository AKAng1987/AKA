# Markov Phase 2 — HMM + Divergence Engine

**Status: plan only, no code written, no AWS writes made.** Grounded in
`MARKOV_SPEC.md` (architecture decisions) and `MARKOV_PHASE1_PLAN.md`
(what's actually live: two independent CTMC generators for
`compass_US`/`grid_US`, writing to `cmon-stage-backend-regime-signals`
daily at 00:55 UTC). Also checked `MARKOV_BACKLOG.md` — item 3
(`macro_prints_today`) was explicitly deferred to "Phase 2 alongside
the HMM," so it's folded into the divergence-engine section below
rather than left as a separate backlog item.

This document does not touch Phase 1 code or infrastructure. All Phase
1 fields, tables, and Lambdas are assumed frozen and working.

---

## 1. HMM observation layer

The HMM is the *market-implied* leading indicator — it should only see
things the market is pricing in real time, not the print-confirmed
macro data the discrete chain already uses (that would just duplicate
Phase 1's signal, not provide a genuinely independent read to diverge
against).

Checked what's already flowing through `cmon-stage-backend-price-history`
/ `metrics-source` before assuming anything needs to be added:

| Observable | Already tracked? | Source | Notes |
|---|---|---|---|
| VIX level + daily Δ | **Yes** | marketstack | No new data needed. |
| 2s10s curve slope + Δ | **Yes** | fred (`T10Y2Y`) | FRED already publishes the spread directly — no need to compute it from raw yields. |
| Sector rotation (SPDR relative performance) | **Yes** | marketstack (11 sectors: XLK/XLF/XLE/XLV/XLI/XLY/XLP/XLU/XLB/XLRE/XLC) | `rrg_data.py` already computes `RS_Ratio`/`RS_Mom` (JdK relative-strength math) for this exact universe — reuse that module's output rather than recomputing sector RS from scratch. |
| Credit spread (HYG−LQD or similar) | **No** | — | Needs two new symbols added to `metrics-source` (HYG, LQD) on `marketstack` — same onboarding pattern as any other ETF already in the pipeline, no Lambda code change (the marketstack Lambda filters its symbol list live from `metrics-source`, same confirmed pattern used for the FX swap). This is the one genuinely new data dependency in this plan. |
| Breadth | **Partial** | — | See §3 — no clean existing series; proxy vs. new provider tradeoff below. |

**Feature vector per day** (proposed): `[vix_level, vix_1d_change,
t10y2y_level, t10y2y_1d_change, sector_rs_dispersion, breadth_proxy]`
— standardized (z-scored against full history) before HMM fitting,
since these are on wildly different scales. `sector_rs_dispersion` is
proposed as cross-sectional std-dev of the 11 sectors' `RS_Ratio` on a
given day (high dispersion = sharp rotation underway, low = broad
risk-on/risk-off move) — a single scalar summary rather than feeding
all 11 sector series in raw, which would make the HMM's dimensionality
unnecessarily large relative to how much history exists.

Two independent HMMs, one per axis (compass, grid) — mirrors Phase 1's
two-chain design rather than reopening the joint-vs-two-chain decision
Phase 1 already settled.

---

## 2. Divergence engine

**The core comparability problem**: the discrete chain's current state
is a single quadrant number (1-4) per axis. An HMM's natural output is
a *probability distribution* over its own latent states — which are
not automatically the same 4 quadrants (see the labeling problem in
§7). Assuming that alignment problem is solved (state *k* of the HMM is
mapped onto "quadrant *k*" via the labeling procedure in §7), the
comparison becomes concrete:

- **`divergence_score`** (scalar, 0–1, per axis) = `1 -
  P_hmm(current_discrete_state)` — how much probability mass the HMM's
  market-implied read assigns to the same state the discrete,
  print-confirmed chain says we're currently in. Near 0 = market
  pricing agrees with the confirmed regime. Near 1 = the market is
  pricing something else entirely.
- **`divergence_direction`** (categorical, per axis) = the HMM's own
  top state when it disagrees (e.g. `discrete says compass=2, HMM's
  top state is compass=3` → market is leaning toward a transition the
  prints haven't confirmed yet). Absent when `divergence_score` is
  below a threshold (see open question in §7 — no threshold is
  proposed here without back-test data to calibrate against).
- **`macro_prints_today`** (folded in from `MARKOV_BACKLOG.md` item 3):
  a same-day snapshot of the raw macro inputs (mirrors
  `macro_prints_at_last_transition`'s shape but taken *now* rather than
  as-of-last-transition) — this is the natural place for it, since it's
  exactly "what does today's data actually look like, and does it
  agree with the regime we're claiming," which only this Lambda (the
  one already reading market-implied state) has a reason to compute
  fresh. Requires giving the new HMM Lambda FRED/BEA read access (a
  new, deliberate scope decision — Phase 1 explicitly rejected this for
  the *existing* signal-updater Lambda; it's being proposed here for a
  *new* Lambda instead, not reopening that rejection).

`divergence_score`/`divergence_direction` are computed per axis
(compass, grid independently) — no cross-axis combination, consistent
with Phase 1 keeping the two axes fully independent throughout.

---

## 3. Breadth data source options

| Option | Cost | Reliability | Effort | Notes |
|---|---|---|---|---|
| FinViz scrape | $0 | Low | Medium | Same class of fragile HTML-scrape already flagged as the most fragile part of the `trading_economics_gdp` client (per memory: hardened once already after a structure change broke it silently). Adds a second fragile scrape to a pipeline that just spent effort removing that failure mode elsewhere. |
| Paid API (Barchart or similar) | $ real cost (typically $50-150+/mo depending on tier/endpoint) | High | Low-Medium | Documented API, SLA-backed. Adds a 3rd paid provider (after MarketStack, and the implicit AV free tier) — recurring cost for a feature that's one of six inputs to one of two HMMs. |
| Custom proxy from existing `price-history` universe | $0 (already-paid data) | High (same infra as everything else) | Low | Advance/decline-style proxy computed from the existing ~41-symbol RRG universe (11 sectors + subsectors + regions) already sitting in `price-history` — e.g. `% of universe with positive 5-day return` as a rough breadth stand-in. **This is ETF/sector-level breadth, not true stock-level market breadth** (thousands of individual names advancing/declining) — a real limitation, not a rounding error. |

**Recommendation: custom proxy from the existing universe.** Reasoning:
zero incremental cost, zero new external dependency (avoids adding a
second fragile scrape right after the silent-failure-trap cleanup, and
avoids a new recurring paid line item for one of six HMM inputs), and
it's directionally informative even though it's not textbook breadth.
Flag explicitly in the HMM's field naming (`breadth_proxy`, not
`breadth`) so nobody downstream mistakes it for NYSE-wide advance/decline
data. Revisit a real breadth provider only if the proxy demonstrably
fails to add signal once there's enough divergence-outcome history to
judge that (see §7 — there isn't yet).

---

## 4. Track-record schema extensions

Read the live table directly (`aws dynamodb scan` on
`cmon-stage-backend-regime-signals`) before proposing names — current
fields, confirmed present on both existing rows:

```
signal_id, signal_date, timestamp_utc, regime_current_discrete,
next_regime_top3, top1_next_probability, transition_entropy,
macro_prints_at_last_transition, n_historical_spells,
total_dwell_days_in_state, spx_close_at_signal
```

**Proposed new fields** (none collide with the above):

```
regime_current_hmm:      {"compass": <int 1-4>, "grid": <int 1-4>}       # HMM's MAP state estimate, same quadrant numbering
hmm_state_probabilities: {"compass": [p1,p2,p3,p4], "grid": [p1,p2,p3,p4]}
hmm_observation_inputs:  {"compass": {...}, "grid": {...}}               # raw feature vector used at signal time, for auditability
divergence_score:        {"compass": <float 0-1>, "grid": <float 0-1>}
divergence_direction:    {"compass": <int 1-4 or absent>, "grid": <int 1-4 or absent>}
macro_prints_today:      {"compass": {...}, "grid": {...}}               # same shape as macro_prints_at_last_transition, taken same-day
```

Written via `UpdateItem` (`add_if_not_exists`-style merge, same
convention `derived_metrics_repo.add_update_entry` already uses) onto
the **same** `(signal_id, signal_date)` row Phase 1's Lambda already
created that morning — not a new row, not a new table. See §6 for why
this dictates the HMM Lambda's run order relative to the existing one.

---

## 5. Estimation window recap

`MARKOV_SPEC.md`'s full-historical-training decision holds unchanged
for the HMM — same reasoning (different macro cycles matter; long-run
vs. recent-window divergence is itself signal).

**Does the 987-day ZIRP dwell / winsorization constraint extend to the
HMM?** No, by construction, **given the observation set proposed in
§1**. Phase 1's winsorization requirement exists because the CTMC
generator explicitly *sums* per-state dwell durations as a denominator
in its exit-rate estimate — a raw 987-day value directly dominates that
sum. A standard HMM fit via Baum-Welch/EM on daily observations doesn't
ingest "days since last transition" as an input feature at all; it only
sees the day's observation vector (VIX, spread, etc.) and *infers*
typical dwell length indirectly, as a property of the fitted
self-transition probability. The 987-day ZIRP episode just contributes
~987 rows of a persistent low-vol regime to the likelihood — legitimate
signal about that regime's emission distribution, not an outlier being
summed.

**This only holds if dwell-time-in-state is never added as an explicit
HMM input feature.** If a later design decision adds "days since last
discrete transition" as one of the observables (some regime-switching
literature does this), the exact same outlier problem returns and the
exact same winsorization/log-transform requirement re-applies.
**Recommendation: don't add it** — the six observables in §1 are
sufficient and this sidesteps the issue entirely rather than requiring
a second, parallel transform pipeline. Flagging as a decision, not
silently assuming it.

---

## 6. Deployment plan

**Recommendation: new Lambda, `cmon-stage-backend-regime-hmm-updater`,
not an extension of `regime-signal-updater`.**

Reasoning:

- **Blast radius.** `regime-signal-updater` is live, working, and
  explicitly called out as untouchable this session
  ("Touch Markov Phase 1 code (working, don't risk it)" — do not
  touch). An HMM fit is meaningfully heavier and more failure-prone
  code (numerical convergence, a new dependency, more moving parts)
  than the sub-second CTMC recompute Phase 1 runs. Bundling it into the
  same function risks a Phase 2 bug taking down Phase 1's working
  daily write.
- **Timing is actually *earlier*, not later.** Phase 1's 00:55 UTC
  slot exists because it depends on `compass-model-updater`/
  `grid-model-updater` (00:25 UTC print-confirmed regime data). The
  HMM's inputs (VIX, sector prices, credit spread) are all
  `marketstack`-sourced and available as soon as `price-updater` runs
  (00:05 UTC) — no dependency on the 00:25 UTC print-confirmed models
  at all. If the HMM Lambda ran standalone it could fire at ~00:15 UTC,
  *before* Phase 1's own signal even exists.
- **But it needs to write onto Phase 1's row, not its own.** Per §4,
  the new fields land on the same `(signal_id, signal_date)` item via
  `UpdateItem`. That means the HMM Lambda must run **after** Phase 1's
  00:55 UTC write has created the row for the day, not before — so the
  natural schedule is a new EventBridge cron at **01:05 UTC** (10 min
  after Phase 1, mirroring the 5-10 min spacing already used between
  Phase 1's two Lambdas), *despite* its own inputs being ready much
  earlier. This is a deliberate ordering choice to keep one row per
  day rather than a race between two `PutItem`s.
- **IAM**: new least-privilege role — Query on `price-history` (VIX,
  sector, credit-spread symbols) + UpdateItem on `regime-signals`. No
  access to `model-history` needed (doesn't touch the discrete side),
  no access to write `compass_US`/`grid_US` (read-only consumer of
  `regime_current_discrete`, which it reads off the row it's updating
  or re-derives from `model-history` the same way Phase 1 does — TBD
  which, flagged in §7).

---

## 7. Risks + open decisions (for your review — not decided here)

1. **HMM state-labeling problem.** A fitted HMM's latent states have no
   inherent names. Mapping "HMM state *k*" onto "quadrant 1-4" needs a
   heuristic (e.g., align each inferred state's occupied-days against
   the discrete chain's logged quadrant history, label by majority
   overlap). This is a real methodological choice, not an
   implementation detail — worth a second look before committing to an
   approach.

   **Resolved (2026-09-10): majority-overlap-by-occupied-days**, exactly
   as sketched above. It's deterministic, auditable against the same
   `model-history` ground truth Phase 1 already trusts, and needs no
   extra library beyond what a `GaussianHMM` fit already requires. Fits
   the project's established "read-source-first, verify against ground
   truth" discipline (FX canary, MACRO port, SPX derived-metrics repair
   all used the same pattern: compute independently, cross-check against
   a trusted reference before trusting the output).

2. **Number of hidden states.** Forcing exactly 4 states (to match the
   quadrant count) vs. letting the HMM discover its own number via a
   model-selection criterion (BIC/AIC) and then mapping post-hoc. Forcing
   4 is simpler and directly comparable to the discrete side; letting it
   be data-driven risks a state count that doesn't map cleanly onto
   quadrants at all.

   **Resolved (2026-09-10): force 4 states.** Mirrors Phase 1's own
   precedent of picking the simpler, directly-comparable design over a
   more "correct" but harder-to-interpret one (two independent 4-state
   chains instead of one joint 16-state model). A data-driven state
   count would also compound directly with decision 1's labeling
   problem — an unknown, possibly-non-4 state count has no clean mapping
   onto "quadrant *k*" at all. Forcing 4 keeps both problems independent
   and tractable.

3. **Emission model choice + library.** A Gaussian-emission HMM
   (`hmmlearn`'s `GaussianHMM`) is the standard fit for this feature
   set. `hmmlearn` is **not** currently in either Lambda dependency
   layer (`cmon-stage-backend-app-dependencies` has numpy/requests/
   marshmallow/etc. but no `hmmlearn`/`scikit-learn`) — adding it means
   a new layer version with meaningfully larger package size. Worth
   confirming Lambda package-size headroom before committing, and
   confirming `hmmlearn`'s license/maintenance status is acceptable for
   a business-critical pipeline component.

   **Resolved (2026-09-10): `hmmlearn`'s `GaussianHMM`, new dedicated
   layer.** License is BSD-3-Clause (permissive, standard for commercial
   use — not a blocker) and the project is actively maintained. Give it
   its **own** layer rather than adding it to
   `cmon-stage-backend-app-dependencies` — that shared layer is exactly
   the kind of shared-artifact surface the 2026-09-08 landmine (stale
   bundled `external_api/` code shipped on one Lambda but not another)
   warned about; an HMM-only layer means only the HMM Lambda needs
   redeployment when this dependency changes. Package-size headroom
   should still be checked at build time, but nothing here blocks
   proceeding.

4. **Breadth proxy quality is an explicit compromise** (§3) — confirm
   the ETF/sector-level proxy is "good enough" to ship with, versus
   paying for a real breadth provider from day one of Phase 2.

   **Resolved (2026-09-10): ship with the proxy**, adopting §3's own
   recommendation — zero incremental cost, zero new fragile dependency
   right after the silent-failure-trap cleanup, and it's one of six
   inputs to the feature vector, not the whole signal. Revisit trigger
   is already well-defined (§3: only if the proxy demonstrably fails to
   add signal once divergence-outcome history exists). Flagging this as
   the single lowest-confidence resolution of the 8 — it's a data-quality
   compromise on a signal whose whole value proposition is a provable
   track record — worth a quick gut-check with the user before Phase 2
   ships, even though nothing here blocks starting the build.

5. **HYG/LQD onboarding** is the one genuinely new data dependency in
   this whole plan (§1) — two new `metrics-source` rows on
   `marketstack`, no Lambda code change needed. Small, but flagging
   explicitly since everything else in this plan reuses data already
   in the pipeline.

   **Resolved (2026-09-10): proceed.** Identical mechanical pattern to
   every prior onboarding this project has done (FX pairs, commodity
   backfills, BTC/ETH re-source) — add the `metrics-source` rows,
   marketstack Lambda picks them up automatically since it filters its
   symbol list live from that table. No new risk class introduced.

6. **`macro_prints_today` reopens the FRED/BEA-credentials-on-a-Lambda
   question** that Phase 1 explicitly declined for the *existing*
   signal-updater (`MARKOV_BACKLOG.md` item 2) — this plan proposes
   granting that access to the *new* HMM Lambda instead, which is a
   different function and a fresh decision, but confirm you're
   comfortable with FRED/BEA credentials existing on any Lambda in this
   pipeline before this gets built.

   **Resolved (2026-09-10): don't grant FRED/BEA credentials at all —
   read the already-cached MACRO data instead.** The Vercel API
   (`~/cgi-vercel/api/macro_data.py`) already fetches and caches FRED/BEA
   data to S3 (`Cache/*`, 6-168h TTL, Phase 2 caching architecture
   confirmed live 2026-09-06/07). Give the HMM Lambda `s3:GetObject` on
   that cache prefix instead of direct FRED/BEA API credentials — same
   least-privilege shape as its other IAM grants (§6: Query on
   `price-history`, UpdateItem on `regime-signals`), sidesteps the
   credential-proliferation question entirely rather than re-litigating
   Phase 1's decision, and reuses infrastructure that's already proven
   live. One caveat: TTL means `macro_prints_today` could be up to
   ~6-168h stale depending on which series it reads — acceptable given
   its stated purpose (a same-day sanity comparison, not a source of
   truth), but worth naming in the field's eventual docstring so nobody
   mistakes it for a live print.

7. **Divergence thresholds are uncalibrated.** `divergence_score` is
   well-defined mathematically, but "what counts as meaningful
   divergence worth surfacing" (for `divergence_direction` to appear,
   or for a future Regime Watch Board to flag it) has no historical
   outcome data to calibrate against yet — Phase 1's outcome-backfill
   only tracks discrete `top3_hit`, not divergence quality. Open
   question: does Phase 2 need its own outcome-tracking extension (did
   high divergence actually precede a regime change more often than
   low divergence did?), or is that explicitly deferred to a Phase 3
   "does the divergence signal actually work" evaluation once enough
   history accumulates? Recommend deferring — there's no data to
   evaluate it against on day 1 of Phase 2 either.

   **Resolved (2026-09-10): defer, adopting the recommendation already
   in this item.** Write `divergence_score`/`divergence_direction` raw
   (no threshold-gated suppression) and let a Phase 3 evaluation, once
   enough history accumulates, decide both the threshold and whether a
   dedicated outcome-tracking extension is worth building. Building
   calibration infrastructure against zero data would be guessing, not
   engineering.

8. **Where does the HMM Lambda get `regime_current_discrete` from?**
   Re-derive independently from `model-history` (same lookup Phase 1
   uses), or read it off the row Phase 1 already wrote 10 minutes
   earlier? Re-deriving is more robust to Phase 1 write delays/failures
   on a given day (self-contained); reading Phase 1's row is simpler
   and guarantees consistency with what's already been logged. No
   strong recommendation — flagging as unresolved.

   **Resolved (2026-09-10): read it off Phase 1's row.** Self-consistent
   with §6's own IAM design, which already grants no `model-history`
   access to the HMM Lambda ("No access to model-history needed",
   deliberately). Re-deriving would mean adding that grant back,
   undoing §6's least-privilege reasoning for a robustness benefit that
   only matters if Phase 1's write silently fails — and Phase 1 has run
   clean and hands-off since 2026-09-06 with its own alarm coverage. If
   Phase 1's row is missing when the HMM Lambda runs at 01:05 UTC, the
   correct behavior is to log and skip that day, not to reach around it.

---

## 8. M4 findings (2026-09-15) — HMM approach falsified, Phase 2 re-scoped

**Supersedes §1, §2, §6 and §7 decisions 1–3.** Everything above this
section describes the plan as approved on 2026-09-10; this section
records what the M4 prototype found when that plan met real data, and
the narrower Phase 2 that replaces it. Kept in one document so the
"Resolved" markers in §7 are read in context.

### What was tested

`scripts/markov_phase2_prototype.py` — 4-state GaussianHMM per axis on
7 market features, majority-overlap (Hungarian) labeling against
`model-history` quadrants. `scripts/markov_phase2_classifier.py` —
supervised gradient-boosting and logistic classifiers on 18 features,
walk-forward validated (expanding window, yearly folds 2011–2026,
60-day purge gap). Compass window 2006-07→2026-09, grid 2008-03→2026-09.

Credit-spread feature is FRED `BAA10Y`, not `BAMLH0A0HYM2`: as of
2026-09 FRED serves ICE BofA series only for the trailing ~3 years
(license restriction; `observation_start` is ignored). HYG/LQD stay
onboarded and backfilled (M1/M2) but only cover 2016-09+.

### Results (all out-of-sample unless marked)

| Target | Base rate | GBM lift | Logistic lift |
|---|---|---|---|
| Compass quadrant (4-class) | 40.5% | −2.0% | — |
| Grid quadrant (4-class) | 27.6% | −0.7% | — |
| Compass / Liquidity up | 84.0% | −10.5% | −7.6% |
| **Compass / Credit up** | 40.9% | **+11.3%** | **+5.6%** |
| Grid / Growth up | 52.0% | −3.0% | −0.5% |
| **Grid / Inflation up** | 46.1% | **+10.3%** | **+8.0%** |

HMM (in-sample): compass 39.3% vs 40.3% base, grid 29.5% vs 27.6%.
The HMM's 90% in-sample Q3 recall collapsed to 6.6% out-of-sample.

### Interpretation

- A market-implied *quadrant* is not recoverable from daily market
  features on this history, by any of three model families. §7
  decision 1 (majority-overlap labeling) is falsified; §7 decision 2
  (force 4 states) and 3 (`hmmlearn` layer) are moot.
- Two *axes* carry modest, model-robust out-of-sample signal — the two
  with direct daily market proxies (credit spread + KRE relative
  strength; TIPS breakevens + commodities). Liquidity fails because Fed
  direction is a discrete FOMC event with an 84% "up" base rate over
  the window; Growth fails because daily prices don't cleanly encode
  GDP direction. Quadrant recombination fails because axis errors
  multiply.
- The binding constraint is **46 discrete transitions in 16 years**
  (~50 effective samples), not features or model choice. More features
  will not fix this; more history will.

### Re-scoped Phase 2 (approved 2026-09-15)

Ship two axis-level divergence signals as an **explicitly experimental
layer**, logged daily from day one so the track record can adjudicate
them, with no UI built around them until Phase 3 evaluates outcomes.

- `credit_divergence`: P(credit_up | today's market) vs the discrete
  Credit state read off Phase 1's row (Q1/Q4 → down, Q2/Q3 → up).
- `inflation_divergence`: P(inflation_up | today's market) vs the
  discrete Inflation state (G1/G4 → down, G2/G3 → up).
- Per axis, written as a map on the existing regime-signals row:
  `{p_up, discrete_up, divergence_score = 1 − P(discrete state),
  direction ("market_tighter" / "market_looser" / null), model_version}`
  under a top-level `experimental_divergence` attribute. Nothing in
  Phase 1's fields changes.
- Model: sklearn `HistGradientBoostingClassifier` (logistic as fallback
  if size/latency matters), trained offline on full history, pickled to
  S3 `Cache/markov/models/`, retrained quarterly by hand as transitions
  accumulate. No `hmmlearn`; the `cmon-stage-pythonlibs-hmmlearn:1`
  layer built in M3 goes unused (cheap, no cleanup needed).
- Lambda `cmon-stage-backend-regime-divergence-updater`, 01:05 UTC
  (unchanged from §6 reasoning: after Phase 1's 00:55 write, onto the
  same row via `UpdateItem`). Reads features from `price-history` only.
  `BAA10Y` and `T10YIE` are onboarded to `metrics-source` on
  `source=fred` (collected by `fred-data-updater`) and backfilled, so
  the Lambda holds no FRED credentials — consistent with §7 decision 6.
- Feature engineering lives in one shared module used by both the
  offline trainer and the Lambda, so the two can't drift.
- Divergence thresholds remain uncalibrated (§7 decision 7 stands);
  `direction` is set whenever `p_up` and `discrete_up` disagree at
  0.5, with no suppression, and Phase 3 decides what counts as
  meaningful.

### Still open for Phase 3

Whether +5–11% out-of-sample lift on a binary classifier survives
6 months of live outcome tags. If it does not, retire the layer and
record the negative result; the track record is the point.

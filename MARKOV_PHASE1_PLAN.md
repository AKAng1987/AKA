# Markov Phase 1 — Minimum-Viable Signal Log

**Status: plan only, no code written.** Written for review per your
"plan first, I'll review before you build" instruction. Read
`MARKOV_SPEC.md` in full before this — this plan is Phase 1 of the
architecture recapped there.

## Scope (restating your constraints, so this plan is checkable against them)

**In scope**: discrete Markov transition matrix from `compass_US` +
`grid_US` history, applying `MARKOV_SPEC.md`'s dwell-time constraint;
daily signal computation (today's regime → top-3 next-regime
probabilities); daily write to `cmon-stage-backend-regime-signals`; a
separate daily backfill job that populates `outcome_1w`/`1m`/`3m` on
signals old enough to check. **Out of scope**: HMM, divergence engine,
Regime Watch Board UI — all explicitly deferred to later phases.

---

## Decision point 1 — one model per axis, not a joint 16-state model

The original table spec's fields (`regime_current_discrete`,
`next_regime_top3`) are singular/flat, and the Tesseract framework is
formally 16 joint states (GRID × COMPASS). I considered building one
joint 16-state chain by merging the two event logs into a single
timeline. **Recommending against that for Phase 1** — going with two
independent 4-state chains (compass, grid), nested inside the same
flat field names as maps keyed by axis. Reasons:

- You called Phase 1 "minimum-viable" — a joint model requires
  reconstructing a merged joint-state timeline from two
  asynchronously-updating event logs (compass_US: 71 rows since 2004;
  grid_US: 143 rows since 2008, different dates) before any counting
  can start. That's real data-engineering work a two-chain model
  skips entirely.
- Sample size: two 4×4 matrices get ~54 and ~143 real transitions
  respectively (see Decision point 2 for why compass's real count is
  54, not 71) feeding 4 rows each. A merged 16-state matrix would
  spread roughly the same total event count across 16 rows — much
  sparser, more states with too few observations to trust.
- The schema doesn't actually force the joint design — `{"compass":
  2, "grid": 4}` fits inside `regime_current_discrete` just as well
  as a single joint state number would.

Joint 16-state modeling stays a reasonable Phase 2/3 upgrade once the
two-chain version is validated and more data has accumulated. Flag if
you actually want the joint model now instead — it's a real fork, not
a detail.

---

## Decision point 2 — what "dwell time" means here, made concrete

This needed pinning down: a plain frequency-count transition matrix
(count `i→j` occurrences, normalize) never touches dwell time at all,
which would leave `MARKOV_SPEC.md`'s constraint with nothing to apply
to. So Phase 1 uses a **continuous-time Markov chain (CTMC) generator**
instead of a discrete-step count matrix — the standard, correct way to
estimate transition rates from irregularly-timed event data anyway
(these series are event-driven, not evenly-spaced), and it's exactly
where dwell time becomes a real, load-bearing input:

1. **Reconstruct spells, not rows.** A "spell" is a maximal run of
   consecutive rows with the same quadrant — checked against the real
   data tonight: **16 of `compass_US`'s 71 rows (23%) are
   same-quadrant repeats** (a new row logged because an underlying
   component signal moved, e.g. `drtscilm_trend`, without the quadrant
   itself changing) — these must be merged into one longer spell, not
   miscounted as a transition. **`grid_US` has zero such repeats** —
   every one of its 143 rows is a genuine quadrant change already,
   simpler to handle. This asymmetry is a real property of the two
   feeds' write patterns, not a bug, and the two series need
   (slightly) different handling because of it.
2. **Winsorize spell durations at the 95th percentile**, computed
   separately per axis (compass and grid likely have different typical
   dwell-time distributions). This is where `MARKOV_SPEC.md`'s
   constraint is actually applied — capping the 987-day ZIRP-era
   compass spell (and any other axis's outliers) before it enters a sum.
3. **Estimate exit rates**: for each state *i*, `q(i→j) = count(i→j
   transitions) / sum(winsorized spell durations spent in state i)` —
   the standard MLE for a CTMC generator matrix. Normalize over
   *j ≠ i* to get `P(next = j | leaving i)`.
4. **"Top-3 next regime" is exactly "all 3 other states, ranked"** for
   a 4-state chain — self-persistence isn't a candidate at all in this
   framing (a CTMC's "next state" is by definition where you go *once
   you leave*, not "did you leave"). This resolves a real ambiguity
   cleanly rather than leaving self-transitions as an awkward 4th
   candidate to rank alongside genuine transitions.

Log-transform (the spec's other option) is better suited to feeding
dwell time into a regression/ML feature — not needed here since the
CTMC generator only ever *sums* durations, where winsorization is the
more natural cap. Flagging the choice explicitly rather than silently
picking one, since the spec offered both.

---

## What gets computed daily (Lambda #1)

New Lambda: **`cmon-stage-backend-regime-signal-updater`** (matches the
existing `-updater` naming convention), EventBridge cron, **00:55 UTC**
— confirmed via `aws events list-rules`/`list-targets-by-rule`:
`compass-model-updater` and `grid-model-updater` are both triggered by
fixed EventBridge schedules, both `cron(25 0 * * ? *)` = **00:25 UTC**,
targeting `cmon-stage-backend-compass-model-updater` and
`cmon-stage-backend-grid-model-updater` respectively. No SNS or
on-demand trigger involved for either — pure fixed cron, both
identical, so "the later of the two" is just 00:25 UTC. Per your
instruction (≥30 min after the later one): **00:55 UTC**.

1. Query full `compass_US` and `grid_US` history (same
   `cmon-stage-backend-model-history` table, same Query pattern already
   used in `dashboard_data.py`'s `model_history_fallback` — reusing a
   proven pattern, not inventing a new one).
2. Recompute both CTMC generator matrices fresh, every run — at
   71/143 rows this is a sub-second in-memory computation. No separate
   "trained model artifact" to store, deploy, or go stale; the model
   *is* the full history, recomputed each day. Simplest thing that
   works at this data volume.
3. Fetch **today's current regime** the same way the LIVE tab does
   (latest row per axis, `ScanIndexForward=False, Limit=1`) — this is
   a deliberate consistency choice: the Markov signal's "current
   regime" must never disagree with what the dashboard is showing,
   and reusing the exact lookup `dashboard_data.py` already uses is
   how that's guaranteed rather than merely hoped for.
4. Look up each axis's top-3 next-state probabilities from its matrix.
5. Fetch latest SPX close from `price-history` (confirmed available
   tonight: 2026-09-04, close 7718.6) → `spx_close_at_signal`.
6. `macro_prints_snapshot` — copy the `metadata` map straight off
   today's latest `compass_US`/`grid_US` rows (these already carry
   exactly this kind of detail, e.g. `drtscilm_percent_change`,
   `interest_rate_trend`, `Growth / GDP` — no new data source needed).
7. Write one row: new `signal_id` (uuid4), `signal_date` = today
   (UTC date string), `timestamp_utc` = write time.

**Fields intentionally left unwritten in Phase 1** (schema is
schemaless, so this just means the attribute is absent, not null):
`regime_current_hmm`, `confidence_hmm`, `divergence_flag`,
`divergence_description` — these stay absent until Phase 2 builds the
HMM side and has something real to put there.

### Proposed shape for the axis-nested fields

```
regime_current_discrete: {"compass_quadrant": 2, "grid_quadrant": 4}
confidence_discrete:     {"compass": 0.55, "grid": 0.40}
next_regime_top3: {
  "compass": [{"quadrant": 3, "probability": 0.35}, ...],
  "grid":    [{"quadrant": 1, "probability": 0.40}, ...]
}
```

**Recommended schema addition beyond the original spec**: an
`n_historical_spells` count per axis alongside `confidence_discrete`
(e.g. `{"compass": 12, "grid": 34}`) — with only 54 real compass
transitions across 4 states, some states will have very few observed
spells, and `confidence_discrete`'s probability number alone doesn't
tell a reader *how much data* backs it. `confidence_discrete` itself
is proposed as simply the top-1 probability — a composite/adjusted
score felt like over-engineering for Phase 1; the raw sample-size
field does the "how much should I trust this" job more transparently.

---

## Outcome backfill (Lambda #2, separate)

New Lambda: **`cmon-stage-backend-regime-outcome-backfill`**, daily
cron, proposed adjacent to Lambda #1: **01:00 UTC** (5 min after).

**Real access-pattern note, worth flagging since it revises the
original table design's assumption**: the original schema note said
"no LSI/GSI needed for v1 (queries will be by PK)" — but this backfill
job's actual access pattern is "find signals from ~7/30/90 days ago,"
which is **date-based, not PK-based** (`signal_id` is a UUID, not
queryable by date without a GSI). At current volume (~1 write/day, so
a few hundred rows even years out) a **`Scan` with a
`FilterExpression`** is trivially cheap and the right call for Phase 1
— flagging this now so it's a documented, informed choice rather than
something that looks like an oversight later. A GSI on `signal_date`
becomes worth adding only if this table ever gets much higher write
volume than one signal/day.

- Tolerance windows (not exact-day matches, matching the existing
  `percent_diff_1m/3m` staleness-cap convention already used elsewhere
  in this pipeline — 37/97/187/372-day caps — rather than inventing a
  new convention): **1w: 7–10 days old, 1m: 30–35 days old, 3m: 90–97
  days old**, each conditioned on the corresponding `outcome_*` field
  not already existing (idempotent — safe to rerun, safe to miss a day).
- For each matching signal: look up the actual compass/grid quadrant
  as of the target date (same latest-row-as-of-date pattern), the SPX
  close as of that date, compute SPX return since `spx_close_at_signal`,
  and — the actual point of this whole system — whether the realized
  quadrant was among the predicted top-3, per axis.

```
outcome_1w: {
  computed_at: "...",
  actual_regime: {"compass_quadrant": 1, "grid_quadrant": 4},
  top3_hit: {"compass": true, "grid": true},
  spx_close: 7750.2,
  spx_return_pct: 0.41
}
```

`top3_hit` is the field that actually accumulates into "180+ dated
calls that become newsletter credibility material" 6 months from now
— it's the one number a hit-rate stat rolls up from later.

---

## IAM

Two new least-privilege roles (not reusing an existing Lambda's
broader role), matching the `cgi-vercel-render-svc` least-privilege
pattern from the Vercel migration:

- Signal-updater: Query on `model-history` + `price-history`, PutItem
  on `regime-signals`.
- Outcome-backfill: Scan + UpdateItem on `regime-signals`, Query on
  `model-history` + `price-history`.

---

## Rollout — so day 1 is actually today

1. Build + you review (per your ask).
2. Once approved: deploy both Lambdas + EventBridge rules.
3. **Run Lambda #1 once manually the same day**, rather than waiting
   for tomorrow's 01:00 UTC cron — this is the only way "today" is
   actually day 1 of the track record instead of tomorrow.
4. Before flipping the cron on, manually eyeball the full computed 4×4
   matrix for both axes (I'll print both in full for review) — sanity
   check against domain intuition (e.g. persistence should generally
   dominate; no implausible instant-flip probabilities) before trusting
   it unattended. Data this sparse doesn't support a formal
   train/test split — a manual read is the right amount of rigor here.
5. Outcome backfill naturally has nothing to do until day 7 — no
   special bootstrapping needed there.

---

## Open questions for your review

1. Two-chain vs. joint 16-state model (Decision point 1) — confirm the
   simpler choice, or override.
2. CTMC + winsorization vs. some other treatment (Decision point 2) —
   confirm, or point me at a different intended design.
3. `n_historical_spells` as a schema addition beyond the original spec
   — keep, drop, or replace with a different confidence signal.

~~4. Need the actual compass/grid-model-updater run time~~ — **resolved**:
both confirmed via `list-rules`/`list-targets-by-rule` as fixed
EventBridge cron `cron(25 0 * * ? *)` (00:25 UTC), no SNS/on-demand
trigger for either. Lambda #1 locked to 00:55 UTC, Lambda #2 to 01:00
UTC.

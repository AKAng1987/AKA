# Markov — Backlog

Non-blocking refinements, flagged during review but not required for
track record accumulation to start. Phase 1 (see
`MARKOV_PHASE1_PLAN.md`) is live and writing daily as of 2026-09-06 —
these items don't hold that up.

## 1. Refactor `confidence_discrete` → `top1_next_probability` + add `transition_entropy` — DONE (2026-09-06)

**Flagged**: 2026-09-06, Claude.ai review of the day-1 signal row.
**Status**: Shipped as planned, no revision. Deployed as
`regime-signal-updater` v2 (`live` alias repointed), day-1
(2026-09-06) row corrected in place via `signal_id_override` (same
`signal_id`, not a new row).

`confidence_discrete` used to just hold the top-1 predicted probability
— the name implied a calibrated confidence *score*, but it was really
just "how likely is the single most-likely next state." Renamed to
`top1_next_probability` (says what it actually is), and added
`transition_entropy` (Shannon entropy in bits over the full per-axis
next-state distribution) as the real confidence signal — low entropy =
concentrated/confident, high = spread-out/uncertain. Day-1 values:
compass 0.981941 bits (near-even 58/42 split, low confidence), grid
1.483659 bits (close to the 3-way max of 1.585, very spread-out).

## 2. `macro_prints_snapshot` rename — DONE (2026-09-06), `_today` fetch REJECTED

**Flagged**: 2026-09-06, Claude.ai review of the day-1 signal row.
**Original proposal**: split into `_at_last_transition` + `_today`
(fresh FRED/BEA read at signal time).
**Revised decision (2026-09-06, same day)**: rename only. Do **not**
add a live `_today` fetch, do **not** add FRED/BEA credentials to this
Lambda.

**Why the revision**: the rename alone (`macro_prints_snapshot` →
`macro_prints_at_last_transition`) already fixes the actual problem —
honest labeling of what the field is (state as of the last regime
change, not a live read). A live `_today` fetch turned out to be more
involved than first assumed: this Lambda has **zero** environment
variables and no FRED/BEA HTTP client at all (confirmed via
`get-function-configuration` before building — the original ask assumed
credentials that don't exist on this function). Building it properly
would mean either a FRED-mirrored proxy for grid's GDP figure (not
confirmed to match `grid-model-updater`'s exact BEA source) or
provisioning a new `BEA_API_KEY` secret onto a Lambda that doesn't need
it for anything else. Current macro state is already served by the
LIVE/MACRO Vercel tabs, so a duplicate fetch here is maintenance burden
without a clear reader who needs it from *this* table specifically.

**Deferred to Phase 2** — see below.

## 3. `macro_prints_today` (fresh macro snapshot alongside the signal) — DEFERRED

**Flagged**: 2026-09-06 (split out from item 2 above).
**Status**: Deferred, not scheduled. Revisit alongside Phase 2's HMM.

Not needed for Phase 1 track-record credibility — the track record's
value is the discrete transition-matrix call and its outcome, not a
macro data mirror. Redundant with the existing LIVE/MACRO tab display.
The natural home for "what does today's data actually look like, and
does it agree with the regime we're claiming" is Phase 2's HMM +
divergence engine (`MARKOV_SPEC.md`'s "priced-in vs. neglect" layer) —
that's already the component whose entire job is comparing a live
market-implied read against the discrete print-confirmed state, so a
fresh macro snapshot belongs there rather than duplicated into Phase 1.

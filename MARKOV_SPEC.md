# Markov Regime Model — Spec Notes

Status: **architecture decisions only, no implementation started.**
Started 2026-09-06 to capture the first concrete constraint to fall out
of a real data audit, rather than leaving it to fall through the cracks
before implementation begins. Not a full spec yet — add to this as more
decisions get made.

## Recap of standing architecture decisions (context, not new)

- **Discrete Markov (print-confirmed regime) + HMM (market-implied
  leading indicator), layered** — divergence between the two is itself
  the "priced-in vs. neglect" signal, not noise to reconcile away.
- **Full historical training**, not truncated — different macro cycles
  matter, and divergence between long-run and recent-window behavior is
  diagnostic signal in its own right.
- Output: three-panel Regime Watch Board per quadrant (current state +
  confirmation signals + top-3 next-regime probabilities + divergence
  panel).
- **Track record is a hard prerequisite** before any external use — see
  `cmon-stage-backend-regime-signals` (created 2026-09-06, empty,
  ready for signal-logging code once it exists).

## New constraint (2026-09-06/07): dwell-time features need transformation before training

**Source of this constraint**: the read-only `compass_US`/`grid_US` data
quality audit (`overnight-report-20260907.md` Task 2) found
`compass_US`'s largest gap between consecutive logged rows is **987
days** (2009-01-17 → 2011-10-01, spanning the ZIRP-era Fed hold). Both
series are event-driven writes (a new row only appears when an
underlying signal crosses a threshold), not calendar-periodic — so gap
length varies enormously and is not itself an error, but it does mean
**dwell-time-in-state, if used as a Markov training feature, has one
data point that is ~17x the series' second-largest gap** (456 days) and
~2-10x most other gaps.

**Constraint**: any dwell-time-derived feature (e.g. "days since last
transition," used as an input to transition-probability estimation or
as a leading/lagging feature) **must be log-transformed or
winsorized (95th percentile cap) before training** — not fed in as a
raw day-count. Untransformed, the 987-day ZIRP outlier would dominate
whatever transition-probability estimate it contributes to, effectively
letting one macro episode (2009-2011) set the model's prior for "how
long does this regime typically last" far more than its true
information content warrants.

**Still open**: whether the 987-day gap reflects genuine 987-day
regime stability (nothing crossed a threshold) or an unverified
infra gap from that era — noted in the Task 2 audit as worth a human
look, not yet resolved. The transform requirement above holds either
way (it protects against outlier dominance regardless of cause), but
resolving the underlying question would still be worth doing before
this becomes the single largest weight in any dwell-time feature.

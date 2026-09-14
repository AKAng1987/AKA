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

## 4. Phase 2 divergence Lambda: "skipped" is silent — OPEN

**Flagged**: 2026-09-15, post-deploy self-review.
**Status**: Open, low urgency.

`regime-divergence-updater` returns `{"status": "skipped", ...}` (no
exception) when Phase 1's row for the date is missing, inputs are
missing, or features are >5 days stale. That's the §7.8-correct
behaviour, but the new `cmon-stage-regime-divergence-updater-errors`
alarm only fires on the AWS/Lambda `Errors` metric, so a skip day
leaves no alarm. Fix is a CloudWatch metric filter on the log line
`-- abort` / `-- skipping` → custom metric → alarm, same pattern as the
provider `ZeroSymbolsSucceeded` alarms. ~15 min, needs no code change.

## 5. `macro_prints_today` — now clearly Phase 3, not Phase 2

Item 3 above deferred this to "Phase 2's HMM." Phase 2 as shipped
(§8 of `MARKOV_PHASE2_PLAN.md`) is two axis-level logistic classifiers
with no macro-print read at all, and the Lambda deliberately holds no
FRED/BEA credentials. If this is ever built, it reads the MACRO S3
cache (§7.6) — but note that cache's `HY_Spread` is now 3-year only
(FRED ICE BofA restriction, see below).

## 6. MACRO Spreads panel: `HY_Spread` history truncated to 2023-09 — DECISION NEEDED

**Flagged**: 2026-09-15.
**Status**: Live regression, user decision pending.

FRED now serves ICE BofA series (`BAMLH0A0HYM2`, `BAMLC0A0CM`) only
for the trailing ~3 years regardless of `observation_start`. The
2026-09-11 `spreads` cache_version bump (bp fix) forced a refetch on
2026-09-14 06:14 UTC, so `Cache/macro/spreads.json` now has
`HY_Spread` from 2023-09-12 only while `T10Y2Y` still runs 2006→.
Options: (a) accept 3-yr HY OAS; (b) switch the panel to `BAA10Y`
(Moody's Baa − 10Y, 1986→, unrestricted, already in metrics-source +
price-history) — a different series (~1.5% vs HY OAS ~2.7%), so label
and units change. Not a Markov item strictly, but it surfaced here.

## 7. BACKTEST: 11 tickers have no pre-2020/2022 history (vendor boundary) — DECISION NEEDED

**Flagged**: 2026-09-15, while reconciling the user's old Sheets
rankings against the new BACKTEST tab.
**Status**: Root-caused, fix needs a design call.

MarketStack's own `/eod` history begins exactly 2020-06-02 for UNG,
URA, UUP, VNM, WEAT, XHE, XLRE, PBS and 2022-01-03 for JJC, JJN, PIN
(confirmed by paginating to the end; XLU etc. reach the 10-yr plan cap
at 2016-09-14). So those tickers have zero occurrences in the 2017–18
C3×G3 era, which is why the old yfinance-era Sheets show e.g. XLRE
3.65x where the current engine shows nothing. Not a pipeline bug.
Fix = yfinance backfill 2016-09→2020-06 / 2022-01 (precedent:
`scripts/backfill_spx_rut.py`, May 2026), BUT UNG has multiple reverse
splits and MarketStack `close` is unadjusted — mixing in a yfinance
series needs an explicit split-handling decision (`auto_adjust=False`
+ split_factor, or accept adjusted closes for the backfilled span) or
the engine will see phantom −75% lows. Separately: XLU 2.76x (Sheets)
vs 1.93x (engine) is NOT a formula difference (ratio-of-means,
mean-of-ratios, median, leave-one-out all ≤2.26 on the same 7
periods) — older regime-period vintage or price source; the engine
number is the one verified against Streamlit + TradingView.

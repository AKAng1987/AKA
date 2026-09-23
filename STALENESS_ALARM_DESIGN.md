# Per-Symbol Staleness Alarm — Design Doc

## Problem

Existing 7 provider alarms trigger on `[Provider] 0/N symbols succeeded` — they only fire when the ENTIRE Lambda run gets zero data. If one symbol quietly stops updating while the other N-1 continue succeeding, nothing fires.

Historical near-misses this would have caught:
- SPX 18-month yahoo gap (2024-2025) — 128 of 129 tickers kept succeeding
- FRED DEX* FX stall (2026-08-28 → 09-08) — the DFF/T10Y2Y macro pulls kept working
- BTC/ETH mislabel drift — writes succeeded, just against the wrong metrics-source row

None of these show up in the current alarms because "at least one symbol succeeded" evaluates true.

## Proposed metric

**Metric name:** `SymbolStaleness`
**Namespace:** `CmonStage/Data`
**Dimensions:** `Symbol` (e.g., "SPX", "USDJPY", "BTC")
**Value:** hours since latest `price_date` in `price-history` (or equivalent per-source table) as of measurement time
**Unit:** Count (representing hours)

## Emitter

New Lambda `cmon-stage-backend-staleness-monitor`:
- EventBridge `cron(15 4 * * ? *)` (after all provider Lambdas + derived-metrics + Markov signal have run)
- Scans `metrics-source` for all `enabled=true` rows
- For each symbol: reads latest `price_date` from `price-history` (Query, ScanIndexForward=false, Limit=1)
- Computes `hours_stale = now_utc - latest_price_date_utc_close`
- Emits one `PutMetricData` per symbol into `CmonStage/Data` with `Symbol` dimension

Cost: ~150 PutMetricData calls/day × 30 days = 4,500/mo. CloudWatch custom metrics free tier = 10 metrics; beyond that ~$0.30/metric/mo. Batching (up to 1000 metric_data entries per API call) keeps API cost negligible. Real cost driver = 150 unique metrics × $0.30 ≈ **$45/mo** if we alarm on all. **Mitigation:** only alarm on the 30 highest-value symbols (SPX, DJI, IXIC, RUT, VIX, QQQ, IWM, SPY, DIA, gold, oil, BTC, ETH, plus the 15 FX pairs and 7 yield tenors) → ~$9/mo.

## Alarm rules

Two tiers per monitored symbol:

**Tier 1 (yellow):** `SymbolStaleness > (freq_expected_hours × 2)` for 1 datapoint
- Daily symbols → 48h
- Weekly symbols (some FRED macro) → 336h
- Notify to `cmon-stage-ops-alerts` (existing SNS topic → angarvinkendrick@gmail.com)

**Tier 2 (red):** `SymbolStaleness > (freq_expected_hours × 5)` for 1 datapoint
- Daily symbols → 120h (5 trading days = clear regression, not a market holiday)
- Notify same topic with `[RED]` prefix in alarm name

Expected frequency comes from `metrics-source.frequency` column (`1D`, `1W`, etc.) — the monitor Lambda reads it per symbol.

## What this catches vs current alarms

| Failure mode | Current | Proposed |
|---|---|---|
| Provider Lambda 0/N | ✅ | ✅ |
| One symbol frozen, rest fine | ❌ | ✅ (Tier 1 at 48h) |
| Whole provider frozen upstream | ✅ (0/N) | ✅ (all Tier 1 fires simultaneously — clearer signal) |
| Symbol writes succeed to wrong source | ❌ | ⚠️ partial — catches if it stops writing to the ENABLED row |
| Backfill script fails mid-run | ❌ | ✅ within 48h |

## IAM for the new Lambda

New role `cmon-stage-backend-staleness-monitor-role`:
- Query on `metrics-source`, Query on `price-history` (paginated per-symbol Query, Limit=1)
- `cloudwatch:PutMetricData` on `CmonStage/Data` namespace
- Scoped log-group grant

Zero write access to any DDB table. Fits the least-privilege pattern of the 3 newer Lambdas.

## Rollout

1. Build + deploy Lambda with EventBridge DISABLED
2. Manual invoke once, verify PutMetricData shows up in CW → Metrics
3. Enable EventBridge
4. Watch for 3 days, tune tier thresholds based on real distribution
5. Add SNS subscription for red-tier only initially (yellow → email digest via metric filter later)

## Deferred / not-in-scope

- Alerting on writes-to-wrong-source (would need cross-source symbol tracking; separate design)
- Dashboard visualization of the staleness metrics (nice-to-have, Grafana or CW dashboard, later)
- Per-provider aggregate rollups (can be added as CW math expressions on top, no new emitter needed)

## Effort

~2 hours: Lambda skeleton, metric-emit loop, IAM role, EventBridge, initial 4 alarm creations (SPX, USDJPY as canaries + 2 broader ones). Full 30-symbol coverage another 1 hour of alarm-creation scripting.

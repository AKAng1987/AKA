# Overnight autonomous run — 2026-09-03

Run window: 2026-09-03 ~01:40–02:00 PST (2026-09-02 ~09:40–10:00 UTC). All times below in UTC unless noted.

## TL;DR

1. **Group A migration: fully completed.** DJI dedup done, COAL/NICKEL/LITHIUM/URANIUM swapped to MarketStack, backfilled (67/67/66/66 rows), and derived_metrics repaired (all 4 had corrupted `ema_7d`; `percent_diff_sd_7d` was missing, not wrong — now computed for all 4).
2. **Nothing was skipped** in Part 1 — every stage's auto-proceed gates passed cleanly, no ambiguous states hit.
3. **BTC/ETH mystery solved:** they're on a completely separate Lambda (`crypto-price-updater`) using CoinGecko, not Yahoo at all — their `yahoo_finance` metrics-source label is stale/incorrect metadata, not live routing.
4. **FX root cause found, and it's the same bug as the commodities** — not two separate outages. Yahoo's `/v7/finance/download` endpoint has required auth (crumb+cookie) that this Lambda never implements since **~2024-09-07**, continuously, for every yahoo_finance symbol. The "2026-05-21" commodity break date is just when those symbols were *added* (one manual backfill succeeded, then hit the same standing wall the next day).
5. **Needs your attention:** the yahoo-finance-updater Lambda is silently broken for its entire remaining symbol list (RUT plus nothing else now that DJI/ETFs are migrated) — recommend migrating RUT via the `IWM` proxy or accepting the gap, and separately, migrating FX to FRED's daily exchange-rate series (`DEXJPUS` etc.) rather than trying to fix the Yahoo endpoint. No code changes made — these are recommendations only.

---

## Part 1 — Group A migration

### 1A. DJI triage — decision: dedup only

- `metrics-source(marketstack, DJI)` already existed, `source_symbol=DJI.INDX`.
- `price-history(DJI)` latest row: **2026-09-01, source=marketstack, close=52766.88** — 1 trading day old, well within the 3-day freshness threshold.
- No confusing/duplicate state found.
- **Action taken (02026-09-02T09:41Z):** `DeleteItem` on `metrics-source(yahoo_finance, DJI)`. Confirmed deleted; `marketstack` row untouched and verified still present.
- Two pre-existing orphaned 2023 phantom price-history rows (`symbol="DJI.INDX"`, `symbol="^DJI"`) were noted in an earlier session but are **out of scope for this run** (not part of the DJI/ETF symbol list this run's safety rules cover) — left untouched.

### 1B. Metrics-source swaps — already done, verified

COAL/NICKEL/LITHIUM/URANIUM were already swapped to `marketstack` in a prior session. Verified again this run: each has **exactly one** metrics-source row, source=marketstack, correct `source_symbol` (COAL→COAL, NICKEL→NIKL, LITHIUM→LIT, URANIUM→URA). No action needed.

### 1C. Price-history backfill — all 4 gates passed, all 4 written

Reference schema validated against a live `QQQ` marketstack row (fields match exactly). Overlap check: **zero** existing marketstack rows for any of the 4 symbols before this run (no collision risk). Fetched MarketStack `/eod`, 2026-05-22 → 2026-09-03.

| Symbol | Ticker | Fetched | Anomalies excluded | Written | Date range written | Row count gate | Overlap gate | Before → After total rows |
|---|---|---|---|---|---|---|---|---|
| COAL | COAL | 70 | 3 (2026-06-04 close=0, 06-09/06-10 close=null) | **67** | 2026-05-22 → 2026-09-01 | PASS (67∈[60,90]) | PASS (0 overlap) | 584 → 651 |
| NICKEL | NIKL | 70 | 3 (same 3 dates) | **67** | 2026-05-22 → 2026-09-01 | PASS | PASS | 795 → 862 |
| LITHIUM | LIT | 70 | 4 (same 3 + 2026-06-15) | **66** | 2026-05-22 → 2026-09-01 | PASS (66∈[60,90]) | PASS | 3982 → 4048 |
| URANIUM | URA | 70 | 4 (same 3 + 2026-06-15) | **66** | 2026-05-22 → 2026-09-01 | PASS | PASS | 3908 → 3974 |

**Note:** MarketStack itself has small provider-side gaps on 2026-06-04 (close=0, clearly bad data), 06-09, 06-10 (null close) across all 4 symbols simultaneously, and 06-15 for LIT/URA — excluded from the write rather than writing corrupt zeros/nulls. These remain as small (3-4 day) holes in price-history within the backfilled range. Not fixed this run (conservative default — no retry-fetch attempted per global safety "no recovery loops").

MarketStack did not yet have 2026-09-02 data at run time (confirmed: no symbol anywhere in price-history, including SPX/SPY/QQQ, had a 2026-09-02 row yet) — this is a pipeline-wide data-freshness lag, not specific to our 4 symbols.

### 1D. Derived_metrics repair — all 4 repaired

Triggered `cmon-stage-backend-derived-metrics-updater` via empty-payload invoke (2026-09-02T09:47Z, StatusCode 200). It targets `now() - 1 day` per its own internal clock, which resolved to **2026-09-01** — exactly the last date we just backfilled, so this worked out cleanly. Fresh derived_metrics rows were written for all 4 symbols for 2026-09-01.

**Corruption found:** `ema_7d` was corrupted for all 4, via the identical gap-recursion bug diagnosed for SPX — the Lambda used the last *existing* prior derived_metrics row (2026-05-21, 103 days stale) as if it were "yesterday" for a single EMA smoothing step. Verified arithmetically for COAL: `27.77×0.25 + 24.8521426065×0.75 = 25.5816069549` (the corrupted value, exactly).

**`percent_diff_sd_7d` was absent, not wrong** — production's own logic requires 6 prior rows with `percent_diff_1d` before computing this field; only 1 existed, so it correctly declined to write anything. Repaired anyway using the same windowed-from-price-history methodology (this is additive, not a correction of bad data).

| Symbol | ema_7d before (corrupted) | ema_7d after (repaired) | percent_diff_sd_7d before | percent_diff_sd_7d after (repaired) |
|---|---|---|---|---|
| COAL | 25.5816069549 | **27.2496109429** | (absent) | **1.49113** |
| NICKEL | 15.4143570218 | **14.7126837304** | (absent) | **1.75734** |
| LITHIUM | 82.2592849296 | **75.8231723831** | (absent) | **1.18557** |
| URANIUM | 48.3382148088 | **45.6959022973** | (absent) | **3.22121** |

Repairs applied via targeted `UpdateItem` (only these two fields per symbol; every other field on each row — `sd_7d`, `volume_7d`, `percent_diff_1d/5d/7d/1m/3m/6m/1y` — was untouched, since those are flat-window calcs off price-history and were never affected by the recursion bug). Verified by re-reading each row post-update.

None of these 4 symbols carry `ema_100d`/`ema_200d`/`sma_260d` (not in `CLOCK_MODEL_TICKERS`), consistent with expectations — nothing else needed repair.

**No stage in Part 1 hit a skip condition.** No unexpected errors. No recovery loops needed.

---

## Part 2 — BTC/ETH puzzle (read-only, definitive answer)

**They are not on the Yahoo pipeline at all, despite the metrics-source label.**

- `metrics-source(yahoo_finance, BTC/ETH)` exists with `source_symbol=BTC-USD/ETH-USD` — but this is **stale metadata that nothing reads for the live pipeline.**
- There is a separate, independent Lambda: **`cmon-stage-backend-crypto-price-updater`**, handler `controller.get_latest_crypto_price.lambda_handler`, with its own EventBridge schedule rule (`cmon-stage-backend-Stack-CryptoPriceUpdaterSchedul-1FH7CKMSCPOW1`), running on its own cadence independent of `yahoo-finance-updater`.
- Its code (`usecase/get_latest_crypto_price.py`) uses `CmnConstants.CRYPTO_ASSETS = {'BTC': 'bitcoin', 'ETH': 'ethereum'}` and calls `external_api/coingecko.py`'s `CoinGeckoApi().get_eod_latest(assets)` — **CoinGecko, not Yahoo, and not even reading the yahoo_finance metrics-source row at all** (no `MetricsSourceRepo` dependency in this usecase).
- Confirmed via live data: BTC's price-history rows have `exchange: "coingecko"` and `source: null` (the crypto updater's write payload never sets a `source` field) — directly contradicting the `yahoo_finance` label sitting in metrics-source.

**Root cause of the discrepancy:** at some point BTC/ETH were migrated to a dedicated CoinGecko-based Lambda (probably when Yahoo's crypto ticker support was unreliable), but the old `metrics-source(yahoo_finance, BTC/ETH)` rows were never cleaned up. They're vestigial and misleading — worth deleting for hygiene, but **not done this run** (BTC/ETH are outside this run's symbol scope).

**Recommended next fix (not executed):** delete the two stale `metrics-source(yahoo_finance, BTC)` / `(yahoo_finance, ETH)` rows once you've confirmed nothing else references them, to stop the table from mislabeling a working pipeline as broken.

---

## Part 3 — FX outage diagnostic (read-only, root cause confirmed)

**This is the same failure as the commodity/RUT/DJI breakage — one root cause, not two.** The apparent "2-year-older" FX gap is an artifact of when each symbol last had a successful write, not evidence of a separate incident.

**Confirmed: `yahoo-finance-updater`** (handler `controller.get_latest_yahoo_finance_data.lambda_handler`) is the Lambda fetching FX. Its `external_api/yahoo_finance.py` calls Yahoo's legacy CSV endpoint `https://query1.finance.yahoo.com/v7/finance/download/{symbol}` — no crumb, no session cookie, no auth handshake of any kind in the code.

**Live reproduction (2026-09-02, this run):**
| Symbol tested | No User-Agent | Browser User-Agent |
|---|---|---|
| `JPY=X` (FX) | HTTP 429 | HTTP 401 `{"code":"unauthorized","description":"User is not logged in"}` |
| `CL=F` (commodity future) | — | HTTP 401, identical error |
| `^RUT` (index) | — | HTTP 401, identical error |

All three asset classes fail **identically** right now — confirming a single systemic cause: Yahoo now requires an authenticated session for this endpoint, and this Lambda has never implemented that.

**Historical log evidence (CloudWatch retention is unlimited — logs back to 2023-06 available):**
- **2024-09-06 run:** printed all symbol names (DJI, RUT, SPX, USDAUD…USDTRY) — i.e., writes succeeded. This matches price-history's last-good dates for FX (2024-09-05/06).
- **2024-09-07 run:** zero symbol names printed, fast execution (~570ms), no errors logged — the exact "silent empty failure" signature. This is the actual onset: **between 2024-09-06 and 2024-09-07**, not 2024-09-05 as originally guessed.
- **2026-05-22 run** (the day after COAL/NICKEL/LITHIUM/URANIUM were manually added to yahoo_finance): **identical silent-empty-failure signature** — zero symbols printed, ~600ms, no errors. This is the same standing wall from 2024-09-07, just newly encountered by symbols that didn't exist in the table until 20 months later.

The silence itself is a code-level finding: `get_latest_data()` in `yahoo_finance.py` only checks `if status_code == 200`, and on any non-200 response simply skips the symbol with **no logging at all**. Combined with `get_latest_market_data()` in the usecase only printing for successfully-returned entries, a 100%-failure run produces a clean-looking, error-free CloudWatch log — this is why the breakage went unnoticed for so long.

**Alternative source check:** MarketStack has no forex product on this plan (confirmed in a prior session — `/v1/eod` rejects all FX symbol formats tested). **FRED does publish daily FX rate series** (e.g. `DEXJPUS`, `DEXUSEU`, etc.) and this pipeline already has a working, independent `fred-data-updater` Lambda and `external_api/fred.py` client actively used for 13 other series (`FEDFUNDS`, `T10Y2Y`, etc.) — this is a low-effort, proven-working path that doesn't require touching the broken Yahoo code at all.

**Recommended next fix (not executed):** migrate the 15 FX pairs to FRED's daily exchange-rate series via the existing `fred-data-updater` pipeline, the same way `DFEDTARU`/`T10Y2Y`/etc. already work — rather than attempting to patch Yahoo's auth requirement into `yahoo_finance.py` (would require implementing a full crumb+cookie handshake, which is fragile and against a legacy endpoint Yahoo may deprecate further).

---

## What's left for RUT and the remaining yahoo_finance symbols

Not part of this run's scope, but now fully explained by Part 3's root cause: **RUT** (still on yahoo_finance) and the futures (`COPPER`, `COTTON`, `NATGAS`, `RICE`, `SILVER`, `USOIL`) are broken by the exact same standing Yahoo auth wall, with no MarketStack equivalent (futures) or only a stale/dead MarketStack index feed (RUT.INDX, frozen since 2023-05-03 on MarketStack's own side — confirmed in a prior session). These need a decision from you: accept the `IWM` ETF proxy for RUT, or find a futures data provider for the commodities — both explicitly out of scope for tonight's run.

---

*Report generated 2026-09-02T09:55Z (2026-09-03 01:55 PST) by an autonomous overnight run. No Lambda code was modified. No symbols outside COAL, NICKEL, LITHIUM, URANIUM, DJI were written to. Parts 2 and 3 were strictly read-only investigations.*

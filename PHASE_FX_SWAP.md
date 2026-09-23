# FX Source-Swap Plan — FRED → Alpha Vantage

**Status: plan only, no execution.** Drafted 2026-09-06 ahead of the
Monday 2026-09-08 decision trigger. Execute only if FRED's `DEX*` series
are still frozen at that point — re-check first (`fredgraph.csv` for all
11 series + the `DFF` sanity check, same method as
`overnight-report-20260907.md` Task 4) before running anything below.

## Trigger recap

All 11 `DEX*` FRED series confirmed frozen at `observation_end=2026-08-28`
as of 2026-09-06, while non-FX FRED series (`DFF`) are current through
2026-09-03 — confirmed FX-specific, not a FRED-wide outage. Opened
2026-08-28 (see `project_cgi` memory).

## Scope — 11 pairs

| `symbol` (internal) | current `source_symbol` (FRED) | `invert` today | new AV `source_symbol` |
|---|---|---|---|
| USDCNY | DEXCHUS | false | CNY |
| USDHKD | DEXHKUS | false | HKD |
| USDINR | DEXINUS | false | INR |
| USDJPY | DEXJPUS | false | JPY |
| USDKRW | DEXKOUS | false | KRW |
| USDSGD | DEXSIUS | false | SGD |
| USDCHF | DEXSZUS | false | CHF |
| USDTHB | DEXTHUS | false | THB |
| USDAUD | DEXUSAL | **true** | AUD |
| USDEUR | DEXUSEU | **true** | EUR |
| USDGBP | DEXUSUK | **true** | GBP |

New `source_symbol` follows the existing 4 AV rows' convention exactly
(`IDR`/`PHP`/`RUB`/`TRY` — bare 3-letter code, not `USDIDR`), confirmed
by reading those rows directly from `metrics-source`.

**`invert` drops out entirely for the AV rows.** FRED's `DEXUSEU` etc.
publish EUR/USD (hence `invert:true` to get USD/EUR), but Alpha
Vantage's `FX_DAILY(from_symbol=USD, to_symbol=EUR)` returns the USD-base
rate directly — no inversion needed regardless of which FRED series it's
replacing. Confirmed against the 4 existing AV rows, none of which carry
an `invert` attribute.

---

## 1. Per-pair `metrics-source` swap

`metrics-source`'s composite key is `(source, symbol)` — a bare
`put_item` on the new row without deleting the old one creates a
**second** row for the same `symbol` rather than overwriting (documented
gotcha from the 5A/SPX migration). Same pattern as the SPX
MarketStack migration (2026-09-02): one `transact_write_items` per pair,
DELETE the `(fred, <symbol>)` item + PUT the `(alpha_vantage, <symbol>)`
item atomically, so there's never a moment with zero or two active
sources for a symbol.

```python
import boto3
ddb = boto3.client("dynamodb", region_name="ap-southeast-1")
TABLE = "cmon-stage-backend-metrics-source"

PAIRS = [
    ("USDCNY", "CNY"), ("USDHKD", "HKD"), ("USDINR", "INR"),
    ("USDJPY", "JPY"), ("USDKRW", "KRW"), ("USDSGD", "SGD"),
    ("USDCHF", "CHF"), ("USDTHB", "THB"),
    ("USDAUD", "AUD"), ("USDEUR", "EUR"), ("USDGBP", "GBP"),
]

for symbol, av_symbol in PAIRS:
    ddb.transact_write_items(TransactItems=[
        {"Delete": {"TableName": TABLE, "Key": {
            "source": {"S": "fred"}, "symbol": {"S": symbol},
        }}},
        {"Put": {"TableName": TABLE, "Item": {
            "source": {"S": "alpha_vantage"},
            "symbol": {"S": symbol},
            "source_symbol": {"S": av_symbol},
        }}},
    ])
```

One `transact_write_items` call per pair (DynamoDB transactions cap at
100 items; 11 pairs × 2 items = 22, so this could be a single
transaction across all 11 — kept per-pair above for isolation: if pair 6
fails validation, pairs 1-5 have already succeeded rather than the whole
batch rolling back together, and it's easier to re-run just the failed
one).

**Verify after swap**, per pair: `describe`/`get_item` confirms the
`(fred, symbol)` row is gone and `(alpha_vantage, symbol)` exists with no
`invert` attribute — then the next `derived-metrics-updater` /
`alpha-vantage-updater` cron cycle should naturally pick up the new
symbol list (same mechanism verified for the SPX/Yahoo migration: the
Yahoo Lambda's symbol list is filtered live from `metrics-source`, and
the same filtering pattern is expected — not re-verified against this
specific Lambda's code this session — to apply to `alpha-vantage-updater`
too. **Confirm this by reading the Lambda's actual symbol-list logic
before executing**, rather than assuming by analogy.)

---

## 2. Backfill script

Modeled on `scripts/backfill_spx_marketstack.py`'s conventions:
`DRY_RUN` env var (default `True`, must be explicitly set `False` to
write), region `ap-southeast-1`, table `cmon-stage-backend-price-history`.

**Key simplification vs. a naive one-call-per-missing-day approach**:
Alpha Vantage's `FX_DAILY` endpoint returns a full time series per call
(not one point), so **one API call per pair backfills the entire gap**,
regardless of whether the gap turns out to be 7 or 11 business days by
Monday. Backfill cost is **11 calls total, one-time** — a completely
separate budget from the 15/day steady-state cron cost below, and
trivial against the 25/day cap on its own.

```python
"""
backfill_fx_alpha_vantage.py — one-time backfill of the price-history
gap left by the FRED DEX* freeze (2026-08-28 -> swap date), for the 11
pairs migrating fred -> alpha_vantage per PHASE_FX_SWAP.md.

DRY_RUN=True (default) — fetch, build payloads, validate, report. No writes.
DRY_RUN=False           — write gap rows to DynamoDB.

Row format (confirmed against existing alpha_vantage rows, USDIDR,
read directly from price-history before writing this script):
  symbol        (S) — e.g. "USDJPY"
  date          (S) — "YYYY-MM-DD"
  open/high/low/close (N) — Decimal, fixed-point formatting (DynamoDB's
                             N type rejects exponential notation --
                             same lesson as backfill_spx_marketstack.py)
  source        (S) — "alpha_vantage"
  source_symbol (S) — e.g. "JPY" (bare code, not "USDJPY")
"""
import os
import time
from decimal import Decimal

import boto3
import requests

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
REGION = "ap-southeast-1"
TABLE_NAME = "cmon-stage-backend-price-history"
AV_API_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]

PAIRS = [
    ("USDCNY", "CNY"), ("USDHKD", "HKD"), ("USDINR", "INR"),
    ("USDJPY", "JPY"), ("USDKRW", "KRW"), ("USDSGD", "SGD"),
    ("USDCHF", "CHF"), ("USDTHB", "THB"),
    ("USDAUD", "AUD"), ("USDEUR", "EUR"), ("USDGBP", "GBP"),
]
GAP_START = "2026-08-29"  # day after FRED's last real observation

def fetch_fx_daily(av_symbol: str) -> dict:
    resp = requests.get("https://www.alphavantage.co/query", params={
        "function": "FX_DAILY", "from_symbol": "USD", "to_symbol": av_symbol,
        "outputsize": "compact", "apikey": AV_API_KEY,
    }, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    # Soft-error detection per memory: AV returns HTTP 200 with an
    # {"Information": ...} body on rate-limit -- must check for this
    # explicitly, same pattern already patched into the 7 provider
    # clients (silent-failure trap, Plan 3C).
    if "Information" in body or "Note" in body:
        raise RuntimeError(f"AV soft-error for {av_symbol}: {body}")
    return body.get("Time Series FX (Daily)", {})

def main():
    ddb = boto3.client("dynamodb", region_name=REGION)
    for symbol, av_symbol in PAIRS:
        series = fetch_fx_daily(av_symbol)
        gap_rows = {d: v for d, v in series.items() if d >= GAP_START}
        print(f"{symbol}: {len(gap_rows)} gap rows to backfill "
              f"({min(gap_rows) if gap_rows else '-'} .. "
              f"{max(gap_rows) if gap_rows else '-'})")
        for date, ohlc in sorted(gap_rows.items()):
            item = {
                "symbol": {"S": symbol}, "date": {"S": date},
                "open":  {"N": format(Decimal(ohlc["1. open"]),  "f")},
                "high":  {"N": format(Decimal(ohlc["2. high"]),  "f")},
                "low":   {"N": format(Decimal(ohlc["3. low"]),   "f")},
                "close": {"N": format(Decimal(ohlc["4. close"]), "f")},
                "source": {"S": "alpha_vantage"},
                "source_symbol": {"S": av_symbol},
            }
            if DRY_RUN:
                print("  [DRY RUN] would write:", item)
            else:
                ddb.put_item(TableName=TABLE_NAME, Item=item)
        time.sleep(1)  # matches the 1-req/sec pacing hardcoded in
                        # cmon-stage-backend-alpha-vantage-updater

if __name__ == "__main__":
    main()
```

Not written to `scripts/` yet — pasted here as the reviewable draft per
your "don't commit without review" instruction. Move to
`scripts/backfill_fx_alpha_vantage.py` once approved.

---

## 3. Derived_metrics — checked, not assumed, USDJPY-first canary

**Verified live tonight** (read-only query against
`cmon-stage-backend-derived_metrics`): the latest row for USDJPY,
USDEUR, USDINR, and USDAUD is dated **exactly 2026-08-28** for all four
— matching the FRED freeze start precisely. This means
`derived-metrics-updater` has **not** been recursing on stale/duplicate
data during the freeze (the RICE-style corruption pattern from the
pre-2026-09-04 bug) — it appears to have simply stopped producing new
rows for these symbols once their price-history stopped updating, which
is the safe failure mode, not the dangerous one.

**What happens after backfill**: once several days of price-history
rows land in one batch, the next normal `derived-metrics-updater` cron
run will see a gap of roughly 7-9 trading days between the last derived
row (08-28) and the new latest price-history row. The 2026-09-04
gap-check patch (Plan 3A) routes into the SMA-bootstrap branch when
`gap > 1×N trading days` — for the 7-day EMA fields, a 7-9 day gap is
**right at or just past that threshold**, not comfortably beyond it. Do
**not** trust the patch on this boundary case just because it handled
the larger, comfortably-oversized gaps it was tested against (RICE's
5,200%-error gap was much larger and unambiguous — this one isn't).

### Canary procedure — USDJPY first, not a full-batch backfill

**Why USDJPY**: smallest gap-count pair by the time Monday arrives (it's
one of the two shortest-history-adjusted pairs in the table and, per
the Streamlit app, the most frequently spot-checked FX value already —
easiest to sanity-eyeball against a second source like a live quote).

1. Run the backfill script (§2) for **USDJPY only**
   (`PAIRS = [("USDJPY", "JPY")]`), `DRY_RUN=False`.
2. Run the `metrics-source` swap (§1) for **USDJPY only**.
3. Wait for the next `alpha-vantage-updater` → `derived-metrics-updater`
   cron cycle to produce a fresh USDJPY `derived_metrics` row.
4. **Manually verify** — pull that fresh row's `ema_7d` (and the other
   7-day fields) and independently recompute it with a windowed-pandas
   replica, the same audit discipline used for the 2026-09-01 SPX repair
   (memory: "read Lambda's exact formulas from source... computed
   correct SPX values"):
   - Read `usecase/generate_derived_metrics.py`'s **current** gap-check
     + SMA-bootstrap formula from the actual Lambda source at execution
     time — do not assume it still matches this document's paraphrase
     above, the source is the authority.
   - Pull USDJPY's last N trading-day closes from `price-history`
     (now-backfilled) directly via `boto3`.
   - Reproduce the formula in a local pandas script, independent of the
     Lambda.
   - Compare to the production value.
5. **Decision gate**:
   - Matches within **1e-6** → proceed to batch the other 10 pairs
     (§1/§2, full `PAIRS` list) exactly as planned.
   - Diverges → **halt**. Do not batch the remaining 10 pairs. Report
     the divergence for a decision on whether to adjust the gap-check
     threshold or repair each pair manually (per-pair, same technique as
     the SPX repair — `UpdateItem` only the affected fields, don't touch
     unrelated rows).

---

## 4. Alpha Vantage rate-limit budget

Checked live tonight, not assumed from memory: 4 symbols already run on
Alpha Vantage today (`IDR`, `PHP`, `RUB`, `TRY`).

```
4 (existing daily AV calls)  +  11 (new DEX pairs)  =  15 calls/day
25/day free-tier cap  −  15  =  10/day headroom
```

(Backfill itself is a separate one-time 11-call cost, not part of this
steady-state math — see §2.) 10/day headroom survives comfortably for
now but is tighter than the ~14 previously assumed in memory (that
number missed the 4 calls already in use) — worth keeping in mind before
adding any further FX pairs to Alpha Vantage without either dropping one
of the 4 existing calls or a paid tier.

---

## 5. Rollback — one pair, one command

**Scenario**: a specific pair's Alpha Vantage cron starts failing
repeatedly (e.g. 3 consecutive CloudWatch alarm emails from
`cmon-stage-ops-alerts` — soft-error detection per the Plan 3C
silent-failure patch, so this would be a real, logged failure, not a
silent one). Fastest recovery is the mirror image of §1's swap, scoped
to just that one symbol — not a full-batch re-migration, not a Lambda
rollback.

```python
"""
rollback_fx_pair.py <SYMBOL> — revert one FX pair from alpha_vantage
back to fred. One-command recovery for a specific pair's AV cron
failing repeatedly post-migration.

Usage: python3 rollback_fx_pair.py USDJPY
"""
import sys
import boto3

REGION = "ap-southeast-1"
TABLE = "cmon-stage-backend-metrics-source"

# Original fred source_symbol + invert, from the pre-migration state
# (PHASE_FX_SWAP.md §"Scope" table) -- needed to restore exactly, not
# approximately.
ORIGINAL_FRED = {
    "USDCNY": ("DEXCHUS", False), "USDHKD": ("DEXHKUS", False),
    "USDINR": ("DEXINUS", False), "USDJPY": ("DEXJPUS", False),
    "USDKRW": ("DEXKOUS", False), "USDSGD": ("DEXSIUS", False),
    "USDCHF": ("DEXSZUS", False), "USDTHB": ("DEXTHUS", False),
    "USDAUD": ("DEXUSAL", True),  "USDEUR": ("DEXUSEU", True),
    "USDGBP": ("DEXUSUK", True),
}

def main():
    symbol = sys.argv[1]
    source_symbol, invert = ORIGINAL_FRED[symbol]
    ddb = boto3.client("dynamodb", region_name=REGION)
    fred_item = {
        "source": {"S": "fred"}, "symbol": {"S": symbol},
        "source_symbol": {"S": source_symbol},
    }
    if invert:
        fred_item["invert"] = {"BOOL": True}
    ddb.transact_write_items(TransactItems=[
        {"Delete": {"TableName": TABLE, "Key": {
            "source": {"S": "alpha_vantage"}, "symbol": {"S": symbol},
        }}},
        {"Put": {"TableName": TABLE, "Item": fred_item}},
    ])
    print(f"{symbol}: reverted alpha_vantage -> fred ({source_symbol}, invert={invert})")

if __name__ == "__main__":
    main()
```

This stops the AV cron alarm for that symbol immediately (metrics-source
no longer lists it under `alpha_vantage`, so the next
`alpha-vantage-updater` run won't attempt it) and, on the same
"Lambda filters its symbol list live from `metrics-source`" pattern
already confirmed for the SPX/Yahoo migration, should let
`fred-data-updater` pick the symbol back up on its own next run —
**confirm this specific Lambda actually reads `metrics-source` the same
way before relying on it at 2am on-call**, same caveat as §1. No
backfill needed on rollback: FRED will have kept publishing for that
pair the whole time (this rollback is for an AV-side failure, not a
recurrence of the original FRED freeze), so there's no gap to fill —
the AV rows written during the pair's time on Alpha Vantage simply stay
in `price-history` as historical fact, no need to delete them.

---

## Execution checklist (Monday, if still frozen)

1. Re-confirm freeze (fredgraph.csv × 11 + DFF sanity check).
2. **Canary: USDJPY only** — backfill (§2, `DRY_RUN=True` then `False`),
   swap (§1), wait one cron cycle, manually verify `ema_7d` against a
   windowed-pandas replica (§3). Do not proceed past this step until it
   matches within 1e-6.
3. **Batch the remaining 10 pairs** — backfill (`DRY_RUN=True` then
   `False`), then the 10 remaining `metrics-source` transact-writes.
4. Spot-check one more pair's `metrics-source` row post-swap (no
   `invert`, correct `source_symbol`).
5. Wait for next `alpha-vantage-updater` + `derived-metrics-updater`
   cron cycle; verify one pair's fresh `derived_metrics` row before
   considering this closed.
6. Keep `rollback_fx_pair.py` (§5) handy for the following few days in
   case any single pair's AV cron starts alarming repeatedly.

"""
cmon-stage-backend-backtest-refresher

Phase 3 (see ~/cgi-vercel/PHASE3_LAMBDA_REWRITE.md for full rationale).
Daily job: recompute BACKTEST tab stats for every ticker in
the backtest universe against every (grid_q, compass_q) regime combo, and
write one S3 blob Render's read path serves from.

Replaces the Render-hosted chunked-refresh design (cgi-vercel/api/
backtest_data.py's refresh_chunk/refresh_tickers + manifest/chunk
files) after production timing tests showed cgi-api-9mim.onrender.com
paying a consistent ~75s TCP-connect delay on every request, including
bare unauthenticated GETs -- a network-layer origin problem, not a
compute-speed one. Moving refresh compute in-region next to DynamoDB
(ap-southeast-1, same as all 12 other backend Lambdas) sidesteps it.

Core compute (build_regime_periods, get_occurrences, compute_stats,
the two fetch functions) is a direct, unchanged port of
cgi-vercel/api/backtest_data.py's equivalents.

THE TICKER UNIVERSE IS FETCHED, NOT BUNDLED (changed 2026-09-26)
----------------------------------------------------------------
This Lambda used to bundle its own copy of HUD_GROUPS with a comment
saying "keep in sync manually if HUD_GROUPS ever changes there". It
drifted: the API declared 167 tickers, this copy produced 129, and the
38-ticker difference -- EWQ plus 31 sector and 6 commodity ETFs -- was
silently never backtested. Those tickers appeared in the dashboard's
own universe and had no regime stats at all. EWQ was not a data gap; it
has 5,318 rows of price history back to 2005, more than EPHE.

So the universe now comes from GET /api/universe, which derives it from
the one authoritative HUD_GROUPS. The alternative -- one shared file
both deploys read -- was rejected because cgi-vercel and market-dashboard
are separate repos with deliberately separate deployment artifacts (see
the external_api/ landmine noted 2026-09-08), so "shared" would mean a
cross-repo copy step at build time: another sync mechanism that can
drift silently, which is the defect being fixed.

The bundled list is KEPT as a fallback, because this Lambda exists
precisely because Render's request path is unreliable (the ~75s TCP
delay below), and a hard dependency on it would let a Render outage kill
the nightly refresh entirely. A fallback, though, can itself go stale
silently -- which is the original defect wearing a different hat. So the
blob records which source was used, and cgi-vercel's
backtest_data.universe_drift() compares the blob's tickers against the
live universe on EVERY read and surfaces the difference. Drift is now
loud wherever it comes from.

Still no custom layer: urllib is in the standard library.

Full-universe, single-shot, no chunking: a Lambda in this region has
no per-request timing ceiling to chunk around. Storage simplifies to
one blob, Cache/backtest/occurrences.json:
  {schema_version, last_refreshed_at, tickers: {ticker: {group, combos}}}
combos keyed "{grid_q}_{compass_q}" -> list of occurrence dicts, same
shape as the chunked design used per-chunk.

Per-ticker try/except resilience is kept (one bad ticker or one
AccessDenied/throttle on a single symbol shouldn't kill the whole
run) -- this was a real bug caught and fixed during the chunked
build's testing, orthogonal to chunking, carried forward here.

Event payload:
  {"dry_run": true}  -- compute everything, write nothing to S3,
                         return the full result for manual inspection.
  {}                 -- normal run: compute AND write the blob.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import date as _date, datetime, timedelta, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"
MODEL_TABLE = "cmon-stage-backend-model-history"
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
BLOB_KEY = "Cache/backtest/occurrences.json"

# Bump whenever the occurrence/stat computation logic changes -- Render
# read-side callers compare this against their own expected version.
SCHEMA_VERSION = 2  # 2: trend-at-entry fields on each occurrence (BACKTEST v2 step 1)

_logger = logging.getLogger("backtest_refresher")
_logger.setLevel(logging.INFO)

_ddb = boto3.client("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)

# FALLBACK ONLY. The live universe comes from UNIVERSE_URL; this copy is used
# only when that fetch fails, so that a Render outage degrades the refresh to
# "last known universe" instead of killing it. It does NOT need manual syncing
# to stay correct -- if it goes stale, universe_drift() on the read side says
# so, loudly, along with universe_source telling you the fallback was used.
HUD_GROUPS: "OrderedDict[str, tuple]" = OrderedDict([
    ("US EQUITIES", (["DJI", "SPX", "IXIC", "RUT", "VIX"], 'SPX')),
    ("INDEX ETF", (["DIA", "SPY", "QQQ", "IWM"], 'SPX')),
    ("SECTOR ETF", (
        [
            "XLB", "XLI", "XLY", "XLC", "XLK", "XME", "XLRE", "XLP", "XLU",
            "XLE", "XOP", "XHB", "PBS", "PBJ", "PEJ", "TAN", "ICLN", "XLF",
            "KBE", "KRE", "KIE", "IAI", "XLV", "XHE", "IYT", "JETS", "BLOK",
            "SOCL", "SOXX", "ROBO", "SKYY", "FDN", "HACK", "CIBR", "KWEB", "MJ",
            "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ", "IZRL", "SMH", "MAGS",
            "AIQ", "WCLD", "IBB", "IHI", "IHE", "IYH", "CNCR", "ITA", "XAR",
            "VNQ", "MORT", "XRT", "IBUY", "KCE", "KBWP", "PSP", "ESPO", "BJK",
            "VICE", "IDRV", "KARS", "BATT", "GRID", "FAN", "PBD", "NLR", "EVX",
            "SEA", "IGF"
        ],
        'SPX',
    )),
    ("US INTEREST RATES", (
        [
            "US03MY", "US01Y", "US02Y", "US05Y", "US10Y", "US20Y", "US30Y",
            "MOVE"
        ],
        None,
    )),
    ("BONDS ETF", (["SHY", "IEF", "TLT", "TMF"], 'SPX')),
    ("SPREADS", (["T10Y2Y", "T10Y3M"], None)),
    ("RATES", (["DFEDTARU", "FEDFUNDS", "CPIAUCSL", "GDP", "DRTSCILM"], None)),
    ("COMMODITIES METALS", (
        [
            "DBC", "USO", "UNG", "GLD", "GDX", "GDXJ", "SLV", "SIL", "JJC",
            "CPER", "JJN", "WOOD", "SLX", "URA", "COPX", "IEO", "OIH", "FCG",
            "MLPX", "CRAK"
        ],
        'USCI',
    )),
    ("COMMODITIES CONT.", (
        [
            "USCI", "USOIL", "NATGAS", "GOLD", "SILVER", "COPPER", "NICKEL",
            "LITHIUM", "SLX", "WOOD", "URANIUM", "COAL"
        ],
        'USCI',
    )),
    ("AGRICULTURAL", (["DBA", "WEAT", "SOYB", "CORN", "RICE", "CANE", "COTTON"], 'DBA')),
    ("COUNTRY ETF", (
        [
            "EWQ", "KWEB", "FXI", "EWJ", "EWZ", "EWT", "EWG", "EWH", "EWI",
            "EWW", "EWU", "PIN", "IDX", "VNM", "EWM", "EIDO", "EPHE", "EWY",
            "EWA", "EWC", "EWS", "EWP", "EWL", "EZA", "INDA"
        ],
        'SPX',
    )),
    ("FOREIGN RATES", (
        [
            "JP10Y", "CN10Y", "HK10Y", "PH10Y", "EU10Y", "GB10Y", "FR10Y",
            "DE10Y", "IT10Y", "ES10Y", "SG10Y", "KR10Y"
        ],
        None,
    )),
    ("FX", (
        [
            "USDPHP", "USDJPY", "USDCNY", "USDAUD", "USDEUR", "USDGBP",
            "USDCHF", "USDSGD", "USDKRW", "USDHKD", "USDIDR", "USDINR",
            "USDRUB", "USDTHB", "USDTRY", "DXY", "UUP"
        ],
        'DXY',
    )),
    ("CRYPTO", (["BTC", "ETH", "BITO"], 'DXY')),
])
BACKTEST_EXCLUDE_GROUPS = frozenset({
    "US INTEREST RATES", "SPREADS", "RATES", "FOREIGN RATES"
})


def _combo_key(grid_q: int, compass_q: int) -> str:
    return f"{grid_q}_{compass_q}"


def _build_backtest_universe() -> tuple[list[str], dict[str, str]]:
    universe: list[str] = []
    ticker_group: dict[str, str] = {}
    seen: set = set()
    for gname, (tickers, _) in HUD_GROUPS.items():
        if gname in BACKTEST_EXCLUDE_GROUPS:
            continue
        for t in tickers:
            if t not in seen:
                seen.add(t)
                universe.append(t)
                ticker_group[t] = gname
    return universe, ticker_group


_FALLBACK_UNIVERSE, _FALLBACK_GROUP_MAP = _build_backtest_universe()

# The authoritative universe. Public and unauthenticated by design (a ticker
# list is not sensitive), same as /api/watchlists.
UNIVERSE_URL = "https://cgi-api-9mim.onrender.com/api/universe"

# Render free-tier instances sleep and pay a documented ~75s TCP-connect delay
# on wake, so this timeout is generous on purpose: the cost of waiting is one
# slow run, the cost of timing out too early is a silently stale universe.
UNIVERSE_TIMEOUT_S = 120


def _resolve_universe() -> tuple[list[str], dict[str, str], str]:
    """(tickers, group_map, source). Never raises: a failure here must degrade
    to the bundled list rather than abort the nightly refresh."""
    try:
        import urllib.request
        with urllib.request.urlopen(UNIVERSE_URL, timeout=UNIVERSE_TIMEOUT_S) as r:
            payload = json.loads(r.read().decode("utf-8"))
        tickers = payload["tickers"]
        groups = payload["groups"]
        if not isinstance(tickers, list) or not tickers:
            raise ValueError("universe payload has no tickers")
        missing_groups = [t for t in tickers if t not in groups]
        if missing_groups:
            raise ValueError(f"universe payload missing groups for {missing_groups[:5]}")
        # A universe far smaller than the bundled one means something is wrong
        # upstream; refusing it keeps a bad deploy from silently shrinking the
        # blob, which is the same class of failure as the drift itself.
        if len(tickers) < len(_FALLBACK_UNIVERSE) * 0.8:
            raise ValueError(
                f"universe shrank implausibly: {len(tickers)} vs bundled {len(_FALLBACK_UNIVERSE)}"
            )
        _logger.info("universe fetched from %s: %d tickers", UNIVERSE_URL, len(tickers))
        return tickers, groups, "api"
    except Exception as exc:  # noqa: BLE001 -- must never abort the refresh
        _logger.error(
            "universe fetch failed (%s), FALLING BACK to the bundled list of %d "
            "tickers -- this may be stale; check universe_drift on the read side",
            exc, len(_FALLBACK_UNIVERSE),
        )
        return list(_FALLBACK_UNIVERSE), dict(_FALLBACK_GROUP_MAP), f"fallback: {exc}"


def _fetch_model_history(model_name: str) -> list[dict]:
    paginator = _ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=MODEL_TABLE,
        KeyConditionExpression="model_name = :m",
        ExpressionAttributeValues={":m": {"S": model_name}},
        ScanIndexForward=True,
    ):
        for item in page["Items"]:
            rows.append({
                "metrics_date": item["metrics_date"]["S"],
                "quadrant": int(item["quadrant"]["N"]),
            })
    return rows


def _fetch_price_history(symbol: str) -> list[dict]:
    paginator = _ddb.get_paginator("query")
    rows = []
    for page in paginator.paginate(
        TableName=PRICE_TABLE,
        KeyConditionExpression="#sym = :s",
        ExpressionAttributeNames={"#sym": "symbol"},
        ExpressionAttributeValues={":s": {"S": symbol}},
    ):
        for item in page["Items"]:
            close = item.get("close", {}).get("N")
            if close is None:
                continue
            high = item.get("high", {}).get("N", close)
            low = item.get("low", {}).get("N", close)
            rows.append({
                "date": item["date"]["S"],
                "close": float(close),
                "high": float(high),
                "low": float(low),
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def build_regime_periods(grid_rows: list[dict], compass_rows: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()

    grid_map = {r["metrics_date"]: r["quadrant"] for r in grid_rows}
    compass_map = {r["metrics_date"]: r["quadrant"] for r in compass_rows}

    all_dates = sorted(set(grid_map) | set(compass_map))
    if not all_dates:
        return []

    periods = []
    gq: Optional[int] = None
    cq: Optional[int] = None

    for i, date in enumerate(all_dates):
        if date in grid_map:
            gq = grid_map[date]
        if date in compass_map:
            cq = compass_map[date]
        if gq is None or cq is None:
            continue

        if i + 1 < len(all_dates):
            next_date = all_dates[i + 1]
        else:
            next_date = today

        end_dt = _date.fromisoformat(next_date) - timedelta(days=1)
        end = end_dt.isoformat()
        if date <= end:
            periods.append({"start": date, "end": end, "grid_q": gq, "compass_q": cq})

    return periods


TREND_LOOKBACK = 20   # trading rows for the pre-entry return
SMA_WINDOW = 50       # trading rows for the moving average at entry


def build_trend_context(prices: list[dict]) -> dict:
    """Per-ticker, once: date -> index, plus the ticker's own distribution
    of 20-row returns so an entry's pre-entry return can be expressed as a
    percentile *of this instrument's history* (BACKTEST v2 step 1,
    MARKOV_BACKLOG.md item 8). "Extended" for VIX and "extended" for TLT
    are very different absolute moves; the percentile makes them
    comparable."""
    idx = {r["date"]: i for i, r in enumerate(prices)}
    closes = [r["close"] for r in prices]
    rets = sorted(
        closes[i] / closes[i - TREND_LOOKBACK] - 1.0
        for i in range(TREND_LOOKBACK, len(closes))
        if closes[i - TREND_LOOKBACK]
    )
    return {"idx": idx, "closes": closes, "rets_sorted": rets}


def _pctile(sorted_vals: list[float], x: float) -> Optional[float]:
    if not sorted_vals:
        return None
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return round(100.0 * lo / len(sorted_vals), 1)


def trend_at_entry(ctx: dict, entry_date: str) -> dict:
    """Fields describing where the instrument was when the regime began.
    All None when there isn't enough prior history."""
    i = ctx["idx"].get(entry_date)
    closes = ctx["closes"]
    out = {"pre_entry_ret_20d": None, "pre_entry_ret_20d_pctile": None, "above_sma50_at_entry": None}
    if i is None:
        return out
    if i >= TREND_LOOKBACK and closes[i - TREND_LOOKBACK]:
        r = closes[i] / closes[i - TREND_LOOKBACK] - 1.0
        out["pre_entry_ret_20d"] = round(r * 100.0, 2)
        out["pre_entry_ret_20d_pctile"] = _pctile(ctx["rets_sorted"], r)
    if i >= SMA_WINDOW - 1:
        sma = sum(closes[i - SMA_WINDOW + 1: i + 1]) / SMA_WINDOW
        out["above_sma50_at_entry"] = bool(closes[i] > sma)
    return out


def get_occurrences(prices: list[dict], periods: list[dict], grid_q: int, compass_q: int,
                    trend_ctx: Optional[dict] = None) -> list[dict]:
    if not prices or not periods:
        return []

    matching = [p for p in periods if p["grid_q"] == grid_q and p["compass_q"] == compass_q]
    if not matching:
        return []

    results = []
    for period in matching:
        pp = [r for r in prices if period["start"] <= r["date"] <= period["end"]]
        if len(pp) < 1:
            continue

        entry_close = pp[0]["close"]
        if not entry_close:
            continue

        exit_close = pp[-1]["close"]
        max_high = max(r["high"] for r in pp)
        min_low = min(r["low"] for r in pp)

        start_d = pp[0]["date"]
        end_d = pp[-1]["date"]
        duration_days = (_date.fromisoformat(end_d) - _date.fromisoformat(start_d)).days

        occ = {
            "start_date": start_d,
            "end_date": end_d,
            "duration_days": duration_days,
            "entry_close": round(entry_close, 4),
            "exit_close": round(exit_close, 4),
            "high_pct": round((max_high / entry_close - 1.0) * 100.0, 2),
            "low_pct": round((min_low / entry_close - 1.0) * 100.0, 2),
            "return_pct": round((exit_close / entry_close - 1.0) * 100.0, 2),
        }
        if trend_ctx is not None:
            occ.update(trend_at_entry(trend_ctx, start_d))
        results.append(occ)

    return results


def compute_stats(occurrences: list[dict]) -> Optional[dict]:
    if not occurrences:
        return None
    count = len(occurrences)
    avg_high = sum(o["high_pct"] for o in occurrences) / count
    avg_low = sum(o["low_pct"] for o in occurrences) / count
    hit_count = sum(1 for o in occurrences if o["return_pct"] > 0)
    hit_rate = hit_count / count * 100.0
    edge = (avg_high / abs(avg_low)) if avg_low < 0 else None
    avg_return = sum(o["return_pct"] for o in occurrences) / count
    return {
        "count": count,
        "avg_high_pct": round(avg_high, 2),
        "avg_low_pct": round(avg_low, 2),
        "hit_rate": round(hit_rate, 1),
        "edge": round(edge, 2) if edge is not None else None,
        "avg_return": round(avg_return, 2),
    }


def _refresh_all(
    periods: list[dict],
    universe: list[str],
    group_map: dict[str, str],
) -> tuple[dict, list[str], list[dict]]:
    """Recompute every ticker in `universe`. Returns
    (ticker_data, refreshed, failed)."""
    ticker_data: dict = {}
    refreshed: list[str] = []
    failed: list[dict] = []

    for sym in universe:
        try:
            prices = _fetch_price_history(sym)
            if not prices:
                raise ValueError(f"no price data found for {sym}")

            trend_ctx = build_trend_context(prices)
            combos: dict[str, list[dict]] = {}
            for gq in range(1, 5):
                for cq in range(1, 5):
                    occ = get_occurrences(prices, periods, gq, cq, trend_ctx=trend_ctx)
                    if occ:
                        combos[_combo_key(gq, cq)] = occ

            ticker_data[sym] = {
                "group": group_map[sym],
                "combos": combos,
            }
            refreshed.append(sym)
        except Exception as exc:
            _logger.error("failed to refresh %s: %s", sym, exc)
            failed.append({"ticker": sym, "error": str(exc)})
            continue

    return ticker_data, refreshed, failed


def lambda_handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run"))

    universe, group_map, universe_source = _resolve_universe()
    _logger.info("starting backtest refresh, dry_run=%s, universe=%d tickers (source=%s)",
                 dry_run, len(universe), universe_source)

    grid_rows = _fetch_model_history("grid_US")
    compass_rows = _fetch_model_history("compass_US")
    periods = build_regime_periods(grid_rows, compass_rows)

    ticker_data, refreshed, failed = _refresh_all(periods, universe, group_map)

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "last_refreshed_at": now_iso,
        # Which list this run used, so a stale fallback is visible in the blob
        # itself rather than only in a CloudWatch log nobody reads.
        "universe_source": universe_source,
        "universe_count": len(universe),
        "tickers": ticker_data,
    }

    result = {
        "schema_version": SCHEMA_VERSION,
        "last_refreshed_at": now_iso,
        "tickers_refreshed": refreshed,
        "tickers_failed": failed,
        "tickers_in_universe": len(universe),
        "universe_source": universe_source,
        "dry_run": dry_run,
    }

    if dry_run:
        _logger.info("dry_run=true, skipping S3 write. refreshed=%d failed=%d", len(refreshed), len(failed))
        # Full payload only in dry-run response, for manual inspection --
        # a real run's CloudWatch log stays a summary, not a 3-4MB dump.
        result["payload_preview"] = {
            "tickers": list(ticker_data.keys())[:5],
            "sample": {k: ticker_data[k] for k in list(ticker_data)[:1]},
        }
        return result

    try:
        _s3.put_object(
            Bucket=BUCKET,
            Key=BLOB_KEY,
            Body=json.dumps(payload, allow_nan=False).encode("utf-8"),
            ContentType="application/json",
        )
    except ClientError as exc:
        _logger.error("S3 write failed: %s", exc)
        raise

    _logger.info("wrote %s. refreshed=%d failed=%d", BLOB_KEY, len(refreshed), len(failed))
    return result

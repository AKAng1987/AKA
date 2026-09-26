"""
Onboard foreign curve tenors from FRED, and register them for nightly upkeep.

WHY FRED AND NOT TRADINGVIEW
PH's curve came from TVC: symbols, which no Lambda maintains -- they need a
Claude routine through the MCP, and that is the fragile leg. FRED series are
refreshed by cmon-stage-backend-fred-data-updater with nobody in the loop, so
wherever FRED has the series it is strictly the better source.

Registering in metrics-source IS the onboarding step. GOLD had price history
and no registration, so nothing ever updated it and it froze for 1,121 days
without anyone noticing. A series written here but not registered is that bug
again.

NAMES ARE TRUTHFUL, INCLUDING THE SUBSTITUTES
Two tenors have no direct series and use a documented proxy. They are stored
under the name of what they ACTUALLY are, never under the name of the thing
they stand in for:

  DE10Y    German 10y, used as the euro-area long rate. The OECD euro-area
           series (IRLTLT01EZM156N) stopped updating in Jan 2026.
  GBSONIA  SONIA, an OVERNIGHT rate, used as the UK front end. The OECD UK
           3-month series (IR3TIB01GBM156N) also stopped in Jan 2026.

Storing German yields as "EU10Y" or an overnight rate as "GB03MY" would be the
same class of error as a mining stock stored as "GOLD". country_data maps the
substitution explicitly and labels it on the page.

GENUINELY ABSENT, recorded so nobody re-derives it:
  CN10Y   no FRED long-rate series for China
  EU03MY  no euro-area short rate still updating
"""
from __future__ import annotations

import datetime as dt
import sys
import urllib.request
from decimal import Decimal

import boto3

REGION = "ap-southeast-1"
PRICE_TABLE = "cmon-stage-backend-price-history"
METRICS_TABLE = "cmon-stage-backend-metrics-source"
SOURCE = "fred"

# cgi_symbol -> (fred_id, frequency, description)
SERIES = {
    "JP10Y":   ("IRLTLT01JPM156N", "1M", "Japan 10Y government bond yield (OECD)"),
    "JP03MY":  ("IR3TIB01JPM156N", "1M", "Japan 3-month interbank rate (OECD)"),
    "KR10Y":   ("IRLTLT01KRM156N", "1M", "Korea 10Y government bond yield (OECD)"),
    "KR03MY":  ("IR3TIB01KRM156N", "1M", "Korea 3-month interbank rate (OECD)"),
    "GB10Y":   ("IRLTLT01GBM156N", "1M", "UK 10Y gilt yield (OECD)"),
    "CN03MY":  ("IR3TIB01CNM156N", "1M", "China 3-month interbank rate (OECD)"),
    # substitutes -- named for what they are
    "DE10Y":   ("IRLTLT01DEM156N", "1M", "Germany 10Y bund yield (OECD) - euro-area proxy"),
    "GBSONIA": ("IUDSOIA",         "1D", "SONIA overnight rate - UK front-end proxy"),
}

# Tested and NOT usable. Recorded so the next person does not spend the call.
UNAVAILABLE = {
    "IRLTLT01EZM156N": "euro-area 10Y: stopped updating 2026-01, use DE10Y",
    "IR3TIB01EZM156N": "euro-area 3M: stopped updating 2026-01, no replacement found",
    "IR3TIB01GBM156N": "UK 3M: stopped updating 2026-01, use GBSONIA (overnight)",
    "IRLTLT01CNM156N": "China 10Y: does not exist on FRED",
}

# A yield outside this is a units error, not a market move.
MIN_Y, MAX_Y = -5.0, 40.0
# Refuse anything whose newest observation is older than this; onboarding a
# discontinued series is how a stale number gets a permanent home.
MAX_AGE_DAYS = 120


# FRED rate-limits the CSV endpoint. Probing ten series in quick succession
# works; doing it twice in twenty minutes gets every subsequent request timed
# out. The failure looks identical to a dead series, which is dangerous -- it
# would have this script record a live series as discontinued. Hence the pause
# between series and the long backoff.
PAUSE_BETWEEN_SERIES_S = 6


def fetch(fred_id: str, attempts: int = 5) -> list[tuple[str, float]]:
    """Retry with backoff rather than treating a throttled connection as a
    missing series. Mistaking a timeout for 'no data' would silently drop a
    tenor, or worse, mark a healthy series unavailable."""
    import time
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last: Exception | None = None
    for i in range(attempts):
        try:
            rows = urllib.request.urlopen(req, timeout=45).read().decode().strip().split("\n")
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i == attempts - 1:
                raise RuntimeError(f"{fred_id}: {attempts} attempts failed ({exc})") from last
            time.sleep(5 * (2 ** i))
    out = []
    for line in rows[1:]:
        parts = line.split(",")
        if len(parts) < 2 or parts[1] in (".", ""):
            continue
        try:
            out.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return out


def main(write: bool) -> None:
    ddb = boto3.resource("dynamodb", region_name=REGION)
    price, metrics = ddb.Table(PRICE_TABLE), ddb.Table(METRICS_TABLE)
    today = dt.date.today()

    import time
    for idx, (sym, (fred_id, freq, desc)) in enumerate(SERIES.items()):
        if idx:
            time.sleep(PAUSE_BETWEEN_SERIES_S)
        try:
            obs = fetch(fred_id)
        except RuntimeError as exc:
            # A throttled fetch is NOT evidence the series is dead. Skip it and
            # say so, rather than recording it as unavailable.
            print(f"THROTTLED {sym}: {exc} -- retry later, not marking unavailable")
            continue
        if not obs:
            print(f"SKIP {sym}: {fred_id} returned nothing")
            continue
        age = (today - dt.date.fromisoformat(obs[-1][0])).days
        if age > MAX_AGE_DAYS:
            print(f"REFUSE {sym}: {fred_id} newest obs {obs[-1][0]} is {age}d old "
                  f"(> {MAX_AGE_DAYS}) -- discontinued, not onboarding")
            continue
        rows, bad = [], 0
        for d, v in obs:
            if not (MIN_Y <= v <= MAX_Y):
                bad += 1
                continue
            rows.append({"symbol": sym, "date": d, "close": Decimal(str(round(v, 4))),
                         "source": SOURCE, "source_symbol": fred_id})
        print(f"{'DRY ' if not write else ''}{sym:8} <- {fred_id:18} {len(rows):5} rows "
              f"{rows[0]['date']} -> {rows[-1]['date']}, last={rows[-1]['close']}"
              + (f"  ({bad} out of range)" if bad else ""))
        if not write:
            continue
        with price.batch_writer(overwrite_by_pkeys=["symbol", "date"]) as b:
            for r in rows:
                b.put_item(Item=r)
        # Registration is the half that GOLD was missing.
        metrics.put_item(Item={"source": SOURCE, "symbol": sym, "source_symbol": fred_id,
                               "description": desc, "frequency": freq})
        print(f"         registered in {METRICS_TABLE}")

    print("\nnot onboarded (tested, unusable):")
    for k, why in UNAVAILABLE.items():
        print(f"  {k:18} {why}")


if __name__ == "__main__":
    main("--write" in sys.argv)

"""
filings_build.py -- insiders (Form 4), activists (13D/13G) and 13F fund holdings for the Fundamentals universe. Zero tokens.

  python filings_build.py --resolve-managers   # print EDGAR filer candidates for each manager in config/13f_managers.json (no upload)
  python filings_build.py --daily [--days N]   # Form 4 and 13D/G from EDGAR's daily index (first run back-fills 90 days); uploads
  python filings_build.py --13f                # latest and prior 13F-HR for the confirmed managers; maps CUSIPs; uploads
  python filings_build.py --dry-run ...        # any mode: fetch and print, upload nothing

Descriptive only. DERIVED rows are stored, never copies of filings. SEC rules: a descriptive User-Agent with a contact, at most 8 requests a second.
The user agent is NOT in this repo: it comes from the environment variable SEC_USER_AGENT or the first line of ~/.cgi/sec_user_agent.txt.

S3 (Cache/fundamentals/filings/): form4.json, activists.json, funds.json, meta.json. The job refuses to overwrite with a near-empty result.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.path.dirname(HERE), "config", "13f_managers.json")
UNIVERSE_URL = "https://cgi-vercel.vercel.app/api/fundamentals"
UNIVERSE_FILE = os.path.expanduser("~/.cgi/filings_universe.json")
UA_FILE = os.path.expanduser("~/.cgi/sec_user_agent.txt")
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
PREFIX = "Cache/fundamentals/filings/"
REGION = "ap-southeast-1"
MIN_INTERVAL = 0.125            # 8 requests a second at most
WINDOW_DAYS = 90
CLUSTER_N, CLUSTER_DAYS = 3, 30  # 3 or more different insiders buying within 30 days (same rule as the PH tab)
ACTIVIST_FORMS = {"SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}
PASSIVE_FORMS = {"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
SUFFIX = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "LLC", "LP", "HOLDINGS", "HOLDING", "GROUP", "THE",
          "COM", "CL", "CLASS", "A", "B", "C", "NEW", "DEL", "ORD", "SHS", "ADR", "SA", "NV", "AG", "SE"}


# ---- user agent and polite fetching -----------------------------------------------------------------------------------------

def load_ua() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua and os.path.exists(UA_FILE):
        ua = open(UA_FILE).readline().strip()
    if not ua:
        raise SystemExit("No SEC user agent. Create ~/.cgi/sec_user_agent.txt with one line: a client name plus your contact "
                         "(or set SEC_USER_AGENT). It is read here and never stored in the repo.")
    return ua


class Sec:
    def __init__(self, ua: str):
        self.ua, self.last = ua, 0.0

    def get(self, url: str, tries: int = 3) -> str | None:
        for i in range(tries):
            wait = MIN_INTERVAL - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
                return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 404 or (e.code == 403 and "daily-index" in url):   # no index file for a holiday: the archive answers 403
                    return None
                if e.code in (429, 500, 502, 503):
                    time.sleep(2 * (i + 1))
                    continue
                raise
            except Exception:  # noqa: BLE001
                time.sleep(1 + i)
        return None


# ---- pure parsers (tested in check_filings_build.py) ------------------------------------------------------------------------

def parse_daily_index(text: str) -> list[dict]:
    """form.idx rows: Form Type | Company Name | CIK | Date Filed | File Name. Fixed width, so split from the right."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\S.*?)\s{2,}(.*?)\s+(\d{3,10})\s+(\d{8}|\d{4}-\d{2}-\d{2})\s+(edgar/data/\S+)\s*$", line)
        if m:
            d = m.group(4)
            d = d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}"
            out.append({"form": m.group(1).strip(), "name": m.group(2).strip(), "cik": int(m.group(3)), "date": d, "path": m.group(5)})
    return out


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _t(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    n = el.find(path)
    return n.text.strip() if n is not None and n.text else None


def form4_rows(xml_text: str, accession: str, filed: str) -> list[dict]:
    """Open-market buys (P) and sales (S) from one Form 4. Grants, exercises, tax withholding and gifts are dropped."""
    try:
        root = _strip_ns(ET.fromstring(xml_text))
    except ET.ParseError:
        return []
    sym, cik = _t(root, "issuer/issuerTradingSymbol"), _t(root, "issuer/issuerCik")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    owners = [_t(o, "reportingOwnerId/rptOwnerName") for o in root.findall("reportingOwner")]
    role = (_t(rel, "officerTitle") or ("Director" if _t(rel, "isDirector") in ("1", "true") else None)
            or ("10% owner" if _t(rel, "isTenPercentOwner") in ("1", "true") else None) or "")
    plan = (_t(root, "aff10b5One") or "").lower() in ("1", "true")
    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _t(tx, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        try:
            sh = float(_t(tx, "transactionAmounts/transactionShares/value") or 0)
            px = float(_t(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append({"symbol": sym, "issuer_cik": int(cik) if cik else None, "owner": owners[0] if owners else None, "role": role,
                    "date": _t(tx, "transactionDate/value"), "filed": filed, "code": code, "shares": sh, "price": px,
                    "value": round(sh * px, 2), "plan_10b5_1": plan, "accession": accession})
    return out


def insider_summary(rows: list[dict], asof: str) -> dict:
    """Per symbol: 90-day bought and sold dollars (10b5-1 sales kept apart), distinct buyers in 30 days, cluster flag (3+ different)."""
    a = dt.date.fromisoformat(asof)
    out: dict = {}
    for r in rows:
        if not r["date"]:
            continue
        age = (a - dt.date.fromisoformat(r["date"])).days
        if age < 0 or age > WINDOW_DAYS:
            continue
        s = out.setdefault(r["symbol"], {"bought": 0.0, "sold": 0.0, "sold_10b5_1": 0.0, "buyers_30d": set(), "n_buys": 0, "n_sales": 0, "last": None})
        if r["code"] == "P":
            s["bought"] += r["value"]; s["n_buys"] += 1
            if age <= CLUSTER_DAYS:
                s["buyers_30d"].add(r["owner"])
        else:
            s["n_sales"] += 1
            s["sold_10b5_1" if r["plan_10b5_1"] else "sold"] += r["value"]
        s["last"] = max(s["last"] or "", r["date"])
    for s in out.values():
        s["distinct_buyers_30d"] = len(s.pop("buyers_30d"))
        s["cluster"] = s["distinct_buyers_30d"] >= CLUSTER_N
        s["bought"], s["sold"], s["sold_10b5_1"] = round(s["bought"], 2), round(s["sold"], 2), round(s["sold_10b5_1"], 2)
    return out


def norm_name(s: str) -> str:
    """Issuer names differ across sources (NVIDIA CORP / NVIDIA CORPORATION / NVIDIA CORP COM): compare on the distinctive words."""
    words = re.sub(r"[^A-Z0-9 ]", " ", s.upper().replace("&", " AND ")).split()
    keep = [w for w in words if w not in SUFFIX]
    return " ".join(keep)


def parse_13f_table(xml_text: str) -> list[dict]:
    try:
        root = _strip_ns(ET.fromstring(xml_text))
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("infoTable"):
        try:
            sh = float(_t(it, "shrsOrPrnAmt/sshPrnamt") or 0)
            val = float(_t(it, "value") or 0)
        except ValueError:
            continue
        out.append({"issuer": _t(it, "nameOfIssuer"), "class": _t(it, "titleOfClass"), "cusip": (_t(it, "cusip") or "").upper(),
                    "value": val, "shares": sh, "type": _t(it, "shrsOrPrnAmt/sshPrnamtType"), "put_call": _t(it, "putCall")})
    return out


def holdings_by_cusip(rows: list[dict]) -> dict:
    """Equity holdings only (shares, no puts/calls), summed per CUSIP (a manager can list a line more than once)."""
    out: dict = {}
    for r in rows:
        if r["type"] != "SH" or r["put_call"]:
            continue
        h = out.setdefault(r["cusip"], {"issuer": r["issuer"], "shares": 0.0, "value": 0.0})
        h["shares"] += r["shares"]; h["value"] += r["value"]
    return out


def parse_13f_list(text: str) -> dict:
    """SEC's official 13(f) securities list (plain text, fixed width): CUSIP cols 1-9, option flag col 10, issuer cols 11-40."""
    out = {}
    for line in text.splitlines():
        if len(line) >= 40 and re.match(r"^[0-9A-Z]{8}[0-9A-Z]", line[:9]) and line[9:10] in (" ", "*"):
            out[line[:9]] = line[10:40].strip()
    return out


def map_to_universe(cusip_names: dict, universe: dict) -> tuple[dict, dict]:
    """CUSIP -> universe symbol by exact match on the normalised issuer name. universe = {symbol: company name}. Returns (map, report)."""
    by_name: dict = {}
    for sym, nm in universe.items():
        by_name.setdefault(norm_name(nm), []).append(sym)
    m = {c: by_name[norm_name(n)][0] for c, n in cusip_names.items() if norm_name(n) in by_name and len(by_name[norm_name(n)]) == 1}
    got = set(m.values())
    return m, {"universe": len(universe), "mapped": len(got), "mapped_share": round(len(got) / len(universe), 3) if universe else None,
               "unmapped": sorted(set(universe) - got)}


def funds_summary(cur: dict, prior: dict, cmap: dict) -> dict:
    """cur / prior = {manager: {cusip: {shares}}}. Per symbol: tracked holders, adds, cuts, new, exited (share counts, quarter on quarter)."""
    out: dict = {}
    for mgr in cur:
        for cusip, h in cur[mgr].items():
            sym = cmap.get(cusip)
            if not sym:
                continue
            s = out.setdefault(sym, {"holders": 0, "adds": 0, "cuts": 0, "new": 0, "exited": 0, "managers": []})
            before = prior.get(mgr, {}).get(cusip, {}).get("shares")
            s["holders"] += 1
            if prior.get(mgr) is not None:
                if before is None:
                    s["new"] += 1
                elif h["shares"] > before * 1.0:
                    s["adds"] += 1
                elif h["shares"] < before:
                    s["cuts"] += 1
            s["managers"].append({"manager": mgr, "shares": h["shares"], "value": h["value"], "prior_shares": before})
    for mgr in prior:
        if mgr not in cur:
            continue
        for cusip, h in prior[mgr].items():
            sym = cmap.get(cusip)
            if sym and cusip not in cur[mgr]:
                out.setdefault(sym, {"holders": 0, "adds": 0, "cuts": 0, "new": 0, "exited": 0, "managers": []})["exited"] += 1
    return out


FIGI_URL = "https://api.openfigi.com/v3/mapping"
FIGI_CACHE = os.path.expanduser("~/.cgi/cusip_tickers.json")
FIGI_BATCH = 10            # OpenFIGI without a key: 10 ids per request, 25 requests per minute
FIGI_PAUSE = 2.6
FIGI_RETRY_DAYS = 30       # a CUSIP with no ticker is asked again after this long
FIGI_OK_TYPES = {"Common Stock", "ADR", "REIT", "ETP", "GDR", "Closed-End Fund", "Open-End Fund", "Preference", "Right", "Unit", "Tracking Stk", "Mutual Fund"}


def figi_pick(data: list | None) -> dict | None:
    """One OpenFIGI answer (a list of listings) -> {ticker, name, type}: the US composite listing (exchCode US) if present."""
    if not data:
        return None
    us = [d for d in data if d.get("exchCode") == "US" and d.get("ticker")]
    d = (us or [x for x in data if x.get("ticker")] or [None])[0]
    return {"ticker": d["ticker"], "name": d.get("name"), "type": d.get("securityType")} if d else None


def figi_cache_load() -> dict:
    try:
        return json.load(open(FIGI_CACHE))
    except Exception:  # noqa: BLE001
        return {}


def figi_cache_save(c: dict) -> None:
    os.makedirs(os.path.dirname(FIGI_CACHE), exist_ok=True)
    json.dump(c, open(FIGI_CACHE, "w"))


def figi_wanted(cusips: list, cache: dict, today: str) -> list:
    """CUSIPs that still need a lookup: never asked, or asked more than FIGI_RETRY_DAYS ago and found nothing."""
    out = []
    for c in cusips:
        e = cache.get(c)
        if e is None:
            out.append(c)
        elif not e.get("ticker") and (dt.date.fromisoformat(today) - dt.date.fromisoformat(e.get("asked", today))).days >= FIGI_RETRY_DAYS:
            out.append(c)
    return out


def figi_fill(cusips: list, cache: dict, budget: int | None = None, today: str | None = None) -> dict:
    """Look the missing CUSIPs up on OpenFIGI (a free public mapping service; only CUSIPs are sent, no personal data). budget = the most
    requests to make this run (None = until done). Progress is saved as it goes, so a stopped run resumes where it left off."""
    today = today or dt.date.today().isoformat()
    todo = figi_wanted(cusips, cache, today)
    n_req = 0
    for i in range(0, len(todo), FIGI_BATCH):
        if budget is not None and n_req >= budget:
            break
        batch = todo[i:i + FIGI_BATCH]
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": c} for c in batch]).encode()
        res = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(FIGI_URL, data=body, headers={"Content-Type": "application/json"})
                res = json.loads(urllib.request.urlopen(req, timeout=60).read())
                break
            except urllib.error.HTTPError as e:
                time.sleep(30 if e.code == 429 else 5 * (attempt + 1))
            except Exception:  # noqa: BLE001
                time.sleep(5 * (attempt + 1))
        n_req += 1
        if res is None or len(res) != len(batch):
            continue
        for c, o in zip(batch, res):
            hit = figi_pick(o.get("data")) if isinstance(o, dict) else None
            cache[c] = {**(hit or {"ticker": None}), "asked": today}
        if n_req % 20 == 0:
            figi_cache_save(cache)
            print(f"  openfigi: {len(cache)} cusips looked up so far", flush=True)
        time.sleep(FIGI_PAUSE)
    figi_cache_save(cache)
    return cache


def holdings_rows(cur: dict, pri: dict, symmap: dict) -> list:
    """Every name held by at least one tracked manager: ticker, issuer, how many hold it, what changed, total value. Needs a prior quarter
    for the added/reduced counts (they are 0 without one)."""
    agg: dict = {}

    def row(c, h):
        return agg.setdefault(c, {"cusip": c, "ticker": symmap.get(c), "name": h["issuer"], "holders": 0, "adds": 0, "cuts": 0, "new": 0,
                                  "exited": 0, "value": 0.0})

    for mgr, hs in cur.items():
        p = pri.get(mgr)
        for c, h in hs.items():
            a = row(c, h)
            a["holders"] += 1
            a["value"] += h["value"]
            if p is not None:
                b = p.get(c, {}).get("shares")
                if b is None:
                    a["new"] += 1
                elif h["shares"] > b:
                    a["adds"] += 1
                elif h["shares"] < b:
                    a["cuts"] += 1
        if p is not None:
            for c, h in p.items():
                if c not in hs:
                    row(c, h)["exited"] += 1
    rows = [r for r in agg.values() if r["holders"] > 0]
    for r in rows:
        r["value"] = round(r["value"])
    return sorted(rows, key=lambda r: (-r["holders"], -r["value"]))


def manager_view(cur_h: dict, pri_h: dict | None, cmap: dict, top: int = 40, keep: set | None = None) -> dict:
    """One manager's book: largest positions (plus any position in the Fundamentals universe), what changed against the prior quarter, and
    what was exited. cur_h / pri_h = {cusip: {issuer, shares, value}} (equity only). Values are dollars."""
    total = sum(h["value"] for h in cur_h.values())
    rows = []
    for cusip, h in cur_h.items():
        before = (pri_h or {}).get(cusip, {}).get("shares") if pri_h is not None else None
        if pri_h is None:
            change = None
        elif before is None:
            change = "new"
        elif h["shares"] > before:
            change = "added"
        elif h["shares"] < before:
            change = "reduced"
        else:
            change = "unchanged"
        rows.append({"cusip": cusip, "issuer": h["issuer"], "symbol": cmap.get(cusip), "shares": h["shares"], "value": h["value"],
                     "weight_pct": round(h["value"] / total * 100, 2) if total else None, "change": change,
                     "change_pct": round((h["shares"] / before - 1) * 100, 1) if before else None})
    rows.sort(key=lambda r: -r["value"])
    kept = rows[:top] + [r for r in rows[top:] if ((r["cusip"] in keep) if keep is not None else r["symbol"])]
    exited = sorted(({"cusip": c, "issuer": h["issuer"], "symbol": cmap.get(c), "prior_value": h["value"]}
                     for c, h in (pri_h or {}).items() if c not in cur_h), key=lambda x: -x["prior_value"])[:10]
    return {"total_value": total, "n_positions": len(cur_h), "top10_share": round(sum(r["value"] for r in rows[:10]) / total * 100, 1) if total else None,
            "holdings": kept, "exited": exited}


def crowd(cur: dict, pri: dict, cmap: dict, top: int = 25) -> dict:
    """Across the tracked managers, for EVERY name (not only the Fundamentals universe): most held, most added, most reduced.
    Counts are managers: added = raised shares or opened a position; reduced = cut shares or exited. Needs a prior quarter per manager."""
    agg: dict = {}

    def row(c, h):
        return agg.setdefault(c, {"cusip": c, "issuer": h["issuer"], "symbol": cmap.get(c), "holders": 0, "adds": 0, "cuts": 0, "new": 0,
                                  "exited": 0, "value": 0.0})

    for mgr, hs in cur.items():
        p = pri.get(mgr)
        for c, h in hs.items():
            a = row(c, h)
            a["holders"] += 1
            a["value"] += h["value"]
            if p is not None:
                b = p.get(c, {}).get("shares")
                if b is None:
                    a["new"] += 1
                elif h["shares"] > b:
                    a["adds"] += 1
                elif h["shares"] < b:
                    a["cuts"] += 1
        if p is not None:
            for c, h in p.items():
                if c not in hs:
                    row(c, h)["exited"] += 1
    rows = list(agg.values())
    held = sorted((r for r in rows if r["holders"] > 0), key=lambda r: (-r["holders"], -r["value"]))[:top]
    added = sorted((r for r in rows if r["adds"] + r["new"] > 0), key=lambda r: (-(r["adds"] + r["new"]), -r["holders"]))[:top]
    reduced = sorted((r for r in rows if r["cuts"] + r["exited"] > 0), key=lambda r: (-(r["cuts"] + r["exited"]), -r["holders"]))[:top]
    return {"most_held": held, "most_added": added, "most_reduced": reduced, "n_managers": len(cur), "n_with_prior": sum(1 for m in cur if pri.get(m) is not None)}


def parse_13d(xml_text: str, form: str) -> dict:
    """Best effort on the structured 13D/G XML (names differ by schema version); anything not found stays None, never guessed."""
    try:
        root = _strip_ns(ET.fromstring(xml_text))
    except ET.ParseError:
        return {"stake_pct": None, "filer": None}
    pct = None
    for tag in ("percentOfClass", "classPercent", "aggregatePercentage"):
        for el in root.iter(tag):
            try:
                pct = float(el.text); break
            except (TypeError, ValueError):
                pass
        if pct is not None:
            break
    filer = None
    for tag in ("reportingPersonName", "nameOfReportingPerson", "filerName"):
        el = next(root.iter(tag), None)
        if el is not None and el.text:
            filer = el.text.strip(); break
    return {"stake_pct": pct, "filer": filer}


# ---- plumbing ---------------------------------------------------------------------------------------------------------------

def universe() -> dict:
    """{symbol: {name, cik}} from the public Fundamentals payload, with a saved copy for when it is down."""
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(UNIVERSE_URL, headers={"User-Agent": "cgi-filings"}), timeout=120))
        u = {c["symbol"]: {"name": c.get("name") or c["symbol"], "cik": c.get("cik")} for c in d["companies"]}
        if u:
            os.makedirs(os.path.dirname(UNIVERSE_FILE), exist_ok=True)
            json.dump(u, open(UNIVERSE_FILE, "w"))
            return u
    except Exception as exc:  # noqa: BLE001
        print(f"universe fetch failed ({exc}); using the saved copy")
    return json.load(open(UNIVERSE_FILE))


def s3():
    import boto3
    return boto3.client("s3", region_name=REGION)


def s3_get(name: str):
    try:
        return json.loads(s3().get_object(Bucket=BUCKET, Key=PREFIX + name)["Body"].read())
    except Exception:  # noqa: BLE001
        return None


def s3_put(name: str, obj: dict) -> None:
    s3().put_object(Bucket=BUCKET, Key=PREFIX + name, Body=json.dumps(obj, separators=(",", ":")).encode(), ContentType="application/json")


def daily_index_url(d: dt.date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{q}/form.{d:%Y%m%d}.idx"


def acc_and_dir(path: str) -> tuple[str, str]:
    m = re.match(r"edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt", path)
    return (m.group(2), f"https://www.sec.gov/Archives/edgar/data/{m.group(1)}/{m.group(2).replace('-', '')}") if m else ("", "")


def fetch_filing_xml(sec: Sec, path: str) -> str | None:
    """The submission .txt holds the whole filing; take the first XML document in it (Form 4 and the 13D/G and 13F schemas are XML)."""
    t = sec.get("https://www.sec.gov/Archives/" + path)
    if not t:
        return None
    m = re.search(r"<XML>\s*(<\?xml.*?)</XML>", t, re.S | re.I) or re.search(r"(<ownershipDocument.*?</ownershipDocument>)", t, re.S)
    return m.group(1) if m else None


def run_daily(sec: Sec, uni: dict, days: int, dry: bool) -> int:
    ciks = {int(v["cik"]): s for s, v in uni.items() if v.get("cik")}
    meta = s3_get("meta.json") or {}
    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    last = meta.get("daily_through")
    if last and not days_forced(sys.argv):
        start = max(start, dt.date.fromisoformat(last) - dt.timedelta(days=2))     # re-pull the last 2 days: late filings, idempotent
    form4 = (s3_get("form4.json") or {}).get("rows", [])
    act = (s3_get("activists.json") or {}).get("rows", [])
    seen4 = {r["accession"] + "|" + str(r["date"]) + "|" + str(r["code"]) + "|" + str(r["shares"]) for r in form4}
    seen_a = {r["accession"] for r in act}
    d, n_idx = start, 0
    while d <= today:
        if d.weekday() < 5:
            txt = sec.get(daily_index_url(d))
            if txt is not None:
                n_idx += 1
                for r in parse_daily_index(txt):
                    sym = ciks.get(r["cik"])
                    if not sym:
                        continue
                    acc, _ = acc_and_dir(r["path"])
                    if r["form"] == "4":
                        xml = fetch_filing_xml(sec, r["path"])
                        for row in (form4_rows(xml, acc, r["date"]) if xml else []):
                            row["symbol"] = sym
                            k = row["accession"] + "|" + str(row["date"]) + "|" + str(row["code"]) + "|" + str(row["shares"])
                            if k not in seen4:
                                seen4.add(k); form4.append(row)
                    elif r["form"] in ACTIVIST_FORMS | PASSIVE_FORMS and acc not in seen_a:
                        xml = fetch_filing_xml(sec, r["path"])
                        extra = parse_13d(xml, r["form"]) if xml else {"stake_pct": None, "filer": None}
                        seen_a.add(acc)
                        act.append({"symbol": sym, "form": r["form"], "passive": r["form"] in PASSIVE_FORMS, "filed": r["date"],
                                    "filer": extra["filer"], "stake_pct": extra["stake_pct"], "accession": acc})
        d += dt.timedelta(days=1)
    cutoff = (today - dt.timedelta(days=WINDOW_DAYS + 5)).isoformat()
    form4 = [r for r in form4 if (r["date"] or "") >= cutoff]
    act = [r for r in act if r["filed"] >= (today - dt.timedelta(days=365)).isoformat()]
    summ = insider_summary(form4, today.isoformat())
    print(f"index days read {n_idx}; Form 4 rows kept {len(form4)}; names with insider activity {len(summ)}; clusters "
          f"{sum(1 for s in summ.values() if s['cluster'])}; 13D/G rows {len(act)} (13D {sum(1 for a in act if not a['passive'])})")
    if dry:
        return 0
    if n_idx == 0:
        print("REFUSING to upload: no daily index could be read")
        return 1
    s3_put("form4.json", {"asof": today.isoformat(), "window_days": WINDOW_DAYS, "rows": form4, "summary": summ})
    s3_put("activists.json", {"asof": today.isoformat(), "rows": act})
    meta["daily_through"] = today.isoformat()
    meta["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    s3_put("meta.json", meta)
    print("uploaded form4.json, activists.json, meta.json")
    return 0


def days_forced(argv: list) -> bool:
    return "--days" in argv


def managers() -> list[dict]:
    return json.load(open(CONFIG))["managers"]


def resolve_managers(sec: Sec) -> int:
    """Print the EDGAR filer candidates for each manager name. Nothing is written: Arvin confirms a CIK in the config file."""
    for m in managers():
        q = urllib.request.quote(m["name"])
        t = sec.get(f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={q}&type=13F-HR&owner=include&count=10&output=atom") or ""
        # one matching filer: <cik> then <conformed-name> in the company-info block; several: an HTML list with CIK= links
        one = re.search(r"<cik>(\d+)</cik>\s*<conformed-name>(.*?)</conformed-name>", t, re.S)
        seen = [(one.group(2), one.group(1))] if one else []
        if not seen:
            h = sec.get(f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={q}&type=13F-HR&owner=include&count=10") or ""
            for cik, nm in re.findall(r"CIK=(\d{10})[^>]*>\s*([^<]{3,80}?)\s*<", h):
                if (nm, cik) not in seen:
                    seen.append((nm, cik))
        print(f"{m['name']:34s} config cik {m.get('cik')}  candidates: " + ("; ".join(f"{n} [{c}]" for n, c in seen[:4]) or "none found"))
    return 0


def recent_13f(sec: Sec, cik: int, n: int = 2) -> list[dict]:
    t = sec.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if not t:
        return []
    r = json.loads(t)["filings"]["recent"]
    out, seen = [], set()
    for i in range(len(r["form"])):
        if r["form"][i] == "13F-HR" and r["reportDate"][i] not in seen:
            seen.add(r["reportDate"][i])
            out.append({"accession": r["accessionNumber"][i], "filed": r["filingDate"][i], "period": r["reportDate"][i]})
    return sorted(out, key=lambda x: x["period"], reverse=True)[:n]


def fetch_info_table(sec: Sec, cik: int, accession: str) -> list[dict]:
    t = sec.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}.txt")
    if not t:
        return []
    m = re.search(r"(<(?:\w+:)?informationTable.*?</(?:\w+:)?informationTable>)", t, re.S)
    return parse_13f_table(m.group(1)) if m else []


def find_13f_list(sec: Sec) -> dict:
    page = sec.get("https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/official-list-section-13f-securities") or ""
    links = sorted(set(re.findall(r'href="([^"]*13flist\d{4}q\d\.txt)"', page)), key=lambda x: re.findall(r"(\d{4}q\d)", x)[0])
    if not links:
        return {}
    url = links[-1] if links[-1].startswith("http") else "https://www.sec.gov" + links[-1]
    return parse_13f_list(sec.get(url) or "")


def run_13f(sec: Sec, uni: dict, dry: bool) -> int:
    mgrs = [m for m in managers() if m.get("cik")]
    if not mgrs:
        print("No manager has a confirmed cik in config/13f_managers.json. Run --resolve-managers, check the matches, then fill in the cik values.")
        return 1
    cur, pri, rows, periods = {}, {}, [], set()
    lists = {m["name"]: recent_13f(sec, int(m["cik"])) for m in mgrs}
    common = max((fl[0]["period"] for fl in lists.values() if fl), default=None)   # the newest quarter anyone has filed
    for m in mgrs:
        fl = lists[m["name"]]
        if not fl:
            print(f"  {m['name']}: no 13F-HR found"); continue
        if fl[0]["period"] != common:
            # never mix quarters in one crowding count: a manager with no filing for the newest quarter is listed, not counted
            rows.append({"manager": m["name"], "cik": m["cik"], "filed": fl[0]["filed"], "period": fl[0]["period"], "n_positions": None,
                         "prior_period": None, "not_filed_for": common})
            print(f"  {m['name']}: latest 13F is for {fl[0]['period']} (not {common}); listed, not counted")
            continue
        cur[m["name"]] = holdings_by_cusip(fetch_info_table(sec, int(m["cik"]), fl[0]["accession"]))
        if len(fl) > 1:
            pri[m["name"]] = holdings_by_cusip(fetch_info_table(sec, int(m["cik"]), fl[1]["accession"]))
        periods.add(fl[0]["period"])
        rows.append({"manager": m["name"], "cik": m["cik"], "filed": fl[0]["filed"], "period": fl[0]["period"],
                     "n_positions": len(cur[m["name"]]), "prior_period": fl[1]["period"] if len(fl) > 1 else None})
        print(f"  {m['name']}: {len(cur[m['name']])} positions, period {fl[0]['period']}, filed {fl[0]['filed']}")
    names = find_13f_list(sec)
    for h in [x for d in (cur, pri) for mg in d.values() for x in mg.items()]:
        names.setdefault(h[0], h[1]["issuer"] or "")
    cmap, rep = map_to_universe(names, {s: v["name"] for s, v in uni.items()})
    summ = funds_summary(cur, pri, cmap)
    for s in summ.values():
        s["managers"] = sorted(s["managers"], key=lambda x: -x["value"])[:8]
    print(f"managers read {len(rows)} of {len(mgrs)}; universe names mapped {rep['mapped']} of {rep['universe']} ({rep['mapped_share']}); "
          f"names held by at least one tracked manager {len(summ)}")
    if dry:
        return 0
    if len(rows) < 0.6 * len(mgrs):
        print("REFUSING to upload: under 60% of the managers could be read")
        return 1
    figi = figi_cache_load()
    allc = sorted({c for d in (cur, pri) for mg in d.values() for c in mg})
    figi = figi_fill(allc, figi, budget=None if "--map-all" in sys.argv else 40)
    symmap = {**{c: v["ticker"] for c, v in figi.items() if v.get("ticker")}, **cmap}      # the universe symbol wins, then OpenFIGI
    by_manager = {m: manager_view(h, pri.get(m), symmap, keep=set(cmap)) for m, h in cur.items()}
    crowd_all = crowd(cur, pri, symmap)
    hold_rows = holdings_rows(cur, pri, symmap)
    n_tick = sum(1 for r in hold_rows if r["ticker"])
    print(f"all holdings: {len(hold_rows)} names held by at least one tracked manager, {n_tick} with a ticker ({round(n_tick / max(len(hold_rows), 1) * 100)}%)")
    s3_put("funds.json", {"asof": dt.date.today().isoformat(), "period": max(periods) if periods else None,
                          "filed_by": max(r["filed"] for r in rows), "managers": rows, "mapping": rep, "summary": summ,
                          "by_manager": by_manager, "crowd": crowd_all,
                          "note": "13F: long US positions of tracked managers, up to 4.5 months old; crowding, not a signal"})
    s3_put("holdings_all.json", {"asof": dt.date.today().isoformat(), "period": max(periods) if periods else None, "n": len(hold_rows),
                                 "n_with_ticker": n_tick, "n_managers": len(cur), "rows": hold_rows,
                                 "note": "Every name held by a tracked manager (13F, long US equity positions). Added/reduced = managers that raised or cut shares."})
    meta = s3_get("meta.json") or {}
    meta["f13_period"] = max(periods) if periods else None
    meta["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    s3_put("meta.json", meta)
    print("uploaded funds.json, meta.json")
    return 0


def main() -> int:
    dry = "--dry-run" in sys.argv
    sec = Sec(load_ua())
    if "--resolve-managers" in sys.argv:
        return resolve_managers(sec)
    uni = universe()
    if "--daily" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else WINDOW_DAYS
        return run_daily(sec, uni, days, dry)
    if "--13f" in sys.argv:
        return run_13f(sec, uni, dry)
    print("modes: --daily [--days N] | --resolve-managers | --13f   (add --dry-run to upload nothing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

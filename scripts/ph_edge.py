"""PSE EDGE (edge.pse.com.ph) reader: fetch and parse disclosures, quarterly/annual results and buy-back reports.

Plain GET requests, HTML only, no login. EDGE's terms allow personal, non-commercial use and forbid storing or
redistributing its data without PSE's written consent, so this keeps only the few figures the CGI Philippines page shows,
plus titles and a link back to the filing (never a copy of a filing). Be polite: PAUSE seconds between requests.

Pure parsers (tested on saved pages in tests/ph_edge_fixtures) are separate from the fetch helpers.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from html.parser import HTMLParser
from typing import Optional

BASE = "https://edge.pse.com.ph"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PAUSE = 2.0

# disclosure form -> chip. Anything not listed is "other"; the EDGE form number is stable, the title is not.
CHIPS = {
    "4-30": "material", "4-32": "material", "4-13": "material", "16-1": "material",
    "4-31": "press",
    "9-1": "buyback",
    "13-1": "insider",
    "6-1": "dividend", "REIT-6-A": "dividend",
    "4-2": "corporate", "4-23": "corporate", "5-1": "corporate", "4-21": "corporate", "4-11": "corporate", "BL-1": "corporate",
    "4-34": "corporate", "4-3": "corporate", "4-4": "corporate", "4-7": "corporate",
    "14-1": "meeting", "7-1": "meeting", "7-2": "meeting", "4-24": "meeting",
    "4-8": "people", "4-25": "people", "4-9": "people",
    "17-8": "ownership", "17-6": "ownership", "17-16": "corporate",      # >5% holders, initial beneficial ownership, tender offers
    "17-1": "results", "17-2": "results",
}
SKIP_FORMS = {"ETF-1", "4-6"}      # daily ETF trading info, contact-detail changes: noise


# ── tables ───────────────────────────────────────────────────────────────────

class _Tables(HTMLParser):
    """All tables as rows of cell texts; <sup> footnote markers are dropped; the first link of a cell is kept as `href`/`onclick`."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self._cell = None
        self._row = None
        self._sup = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"text": "", "tag": tag, "onclick": [], "href": []}
        elif tag == "sup":
            self._sup += 1
        elif tag in ("style", "script"):
            self._skip += 1
        elif tag == "br" and self._cell is not None:
            self._cell["text"] += " "
        if tag == "a" and self._cell is not None:
            if a.get("onclick"):
                self._cell["onclick"].append(a["onclick"])
            if a.get("href"):
                self._cell["href"].append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["text"] = re.sub(r"\s+", " ", self._cell["text"]).strip()
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self.tables:
                self.tables[-1].append(self._row)
            self._row = None
        elif tag == "sup":
            self._sup = max(0, self._sup - 1)
        elif tag in ("style", "script"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._cell is not None and not self._sup and not self._skip:
            self._cell["text"] += data


def tables(html: str) -> list[list[list[dict]]]:
    p = _Tables()
    p.feed(html)
    return p.tables


def num(text: str) -> Optional[float]:
    """'1,234.5' -> 1234.5; '(12)' -> -12; '-' or '' -> None; 'Php15.84B' is handled by money()."""
    t = (text or "").strip().replace(",", "").replace(" ", "")
    if not t or t in ("-", "--"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def money(text: str) -> Optional[float]:
    """'Php15.84B' -> 15.84e9, 'Php 250 million' -> 250e6, '1,234,000' -> 1234000."""
    t = (text or "").replace(",", "").replace(" ", " ")
    m = re.search(r"([\d.]+)\s*(billion|million|thousand|B|M|K)?\b", t, flags=re.I)
    if not m:
        return None
    mult = {"b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}.get((m.group(2) or "").lower(), 1.0)
    try:
        return float(m.group(1)) * mult
    except ValueError:
        return None


def _date(text: str) -> Optional[str]:
    try:
        return dt.datetime.strptime(text.strip(), "%b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


# ── listings ─────────────────────────────────────────────────────────────────

def pages(html: str) -> int:
    """Number of result pages from the '[1 / 6]' marker (0 when absent)."""
    m = re.search(r"\[\s*\d+\s*/\s*(\d+)\s*\]", html)
    return int(m.group(1)) if m else 0


def parse_directory(html: str) -> list[dict]:
    """Company directory rows: cmpy_id, security_id, symbol, name, sector, subsector."""
    out = []
    for t in tables(html):
        for r in t:
            if len(r) < 5 or r[0]["tag"] != "td":
                continue
            m = re.search(r"cmDetail\('(\d+)','(\d+)'\)", " ".join(r[0]["onclick"]))
            if not m:
                continue
            out.append({"cmpy_id": m.group(1), "security_id": m.group(2), "symbol": r[1]["text"], "name": r[0]["text"],
                        "sector": r[2]["text"], "subsector": r[3]["text"], "listed": _date(r[4]["text"])})
    return out


def parse_listing(html: str) -> list[dict]:
    """Disclosure / financial-report listing rows. `announced` is Manila local time, ISO without zone."""
    out = []
    for t in tables(html):
        for r in t:
            if len(r) < 5 or r[0]["tag"] != "td":
                continue
            m_c = re.search(r"cmpy_id=(\d+)", " ".join(r[0]["href"]))
            m_e = re.search(r"openPopup\('([0-9a-f]+)'\)", " ".join(r[1]["onclick"]))
            if not m_c or not m_e:
                continue
            title = r[1]["text"]
            m_a = re.match(r"\[Amend-(\d+)\]\s*(.*)", title)
            try:
                when = dt.datetime.strptime(r[3]["text"], "%b %d, %Y %I:%M %p").isoformat(timespec="minutes")
            except ValueError:
                continue
            out.append({"cmpy_id": m_c.group(1), "company": r[0]["text"], "title": m_a.group(2) if m_a else title,
                        "amend": int(m_a.group(1)) if m_a else 0, "form": r[2]["text"], "edge_no": m_e.group(1),
                        "announced": when, "number": r[4]["text"]})
    return out


def chip(form: str) -> str:
    return CHIPS.get(form, "other")


def viewer_file_id(html: str) -> Optional[str]:
    m = re.search(r"downloadHtml\.do\?file_id=(\d+)", html)
    return m.group(1) if m else None


# ── filings ──────────────────────────────────────────────────────────────────

def _label_and_numbers(row: list[dict]) -> tuple[str, list[Optional[float]]]:
    label, nums, seen_num = [], [], False
    for c in row:
        v = num(c["text"])
        if v is None and not seen_num and not re.fullmatch(r"-+", c["text"] or "x"):
            label.append(c["text"])
        elif v is not None or re.fullmatch(r"-+", c["text"] or ""):
            seen_num = True
            nums.append(v)
    return " ".join(label), nums


def parse_results(html: str) -> Optional[dict]:
    """Form 17-1 (annual) / 17-2 (quarterly) from /downloadHtml.do. Figures are in `units` (EDGE words it several ways: 'In Php Thousands', 'Pesos (in Thousands)'); `scale` is the multiplier to pesos.
    Quarterly: cur/prev are the 3-month columns, ytd/prev_ytd the year-to-date ones; annual: cur/prev are the two years."""
    period = units = None
    rows: dict[str, list] = {}
    kind = None
    for t in tables(html):
        for r in t:
            texts = [c["text"] for c in r]
            joined = " ".join(texts)
            if r and r[0]["tag"] == "th":
                if re.match(r"For the period ended", texts[0], re.I) and len(texts) > 1:
                    period = _date(texts[1])
                if re.match(r"Currency", texts[0], re.I) and len(texts) > 1:
                    units = texts[1]
            if re.match(r"Income Statement", joined) and "Year Ending" in joined:
                kind = "A"
            elif re.match(r"Income Statement", joined):
                kind = "Q"
            label, nums = _label_and_numbers(r)
            lab = label.lower()
            for key, pat in (("revenue", r"^gross revenue"), ("net_income", r"^net income.*after tax"),
                             ("net_income_parent", r"^net income.*attributable"), ("eps", r"^earnings.*per share.*basic")):
                if re.search(pat, lab) and nums and key not in rows:
                    rows[key] = nums
    if period is None:
        for t in tables(html):
            for r in t:
                for c in r:
                    d = _date(c["text"]) if re.fullmatch(r"[A-Z][a-z]{2} \d{2}, \d{4}", c["text"]) else None
                    if d and period is None:
                        period = d
    if "revenue" not in rows or "net_income_parent" not in rows or period is None:
        return None
    kind = kind or ("Q" if len(rows["revenue"]) >= 4 else "A")
    u = (units or "").lower()
    scale = 1e6 if "million" in u else 1e3 if "thousand" in u else 1.0
    out = {"kind": kind, "period": period, "units": units or "", "scale": scale}
    for key, nums in rows.items():
        nums = (nums + [None] * 4)[:4]
        if kind == "Q":
            out[key] = nums[0]
            out[key + "_prev"] = nums[1]
            out[key + "_ytd"] = nums[2]
            out[key + "_prev_ytd"] = nums[3]
        else:
            out[key] = nums[0]
            out[key + "_prev"] = nums[1]
    return out


def yoy(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    """Percent change; None when the base is missing or not positive (a swing from a loss has no meaningful %)."""
    if cur is None or prev is None or prev <= 0:
        return None
    return round((cur / prev - 1) * 100, 1)


DETAIL_CHIPS = {"material", "dividend", "insider", "corporate", "ownership"}


def parse_detail(html: str, chip_name: str) -> dict:
    """The one-line subject every PSE disclosure form carries, plus the structured figures of dividend (6-1) and insider (13-1)
    forms. Stores a title and a few numbers, never the filing text."""
    rows = [[c["text"] for c in r] for t in tables(html) for r in t]
    out: dict = {}
    for i, r in enumerate(rows):
        if r and r[0] == "Subject of the Disclosure" and i + 1 < len(rows) and rows[i + 1]:
            out["subject"] = re.sub(r"\s+", " ", rows[i + 1][0]).strip()[:170]
            break
    if chip_name == "dividend":
        d = {}
        for r in rows:
            if len(r) >= 2:
                k = r[0]
                if k.startswith("Amount of Cash Dividend Per Share"):
                    d["per_share"] = money(re.sub(r"(?i)php|₱|\s", "", r[1]))
                elif k.startswith("Record Date"):
                    d["record"] = _date(r[1])
                elif k.startswith("Payment Date"):
                    d["payment"] = _date(r[1])
                elif k.startswith("Type (Regular"):
                    d["type"] = r[1][:20]
        if d.get("per_share") is not None:
            out["dividend"] = d
    elif chip_name == "insider":
        name = position = None
        txns = []
        for r in rows:
            if len(r) >= 2 and r[0].startswith("Name of Person"):
                name = r[1][:60]
            elif len(r) >= 2 and r[0].startswith("Position"):
                position = r[1][:60]
            elif len(r) >= 5 and _date(r[1]) and r[3] in ("A", "D"):
                sh, px = num(r[2]), num(r[4])
                if sh:
                    txns.append({"date": _date(r[1]), "shares": sh, "side": r[3], "price": px})
        if txns:
            net = sum(t["shares"] if t["side"] == "A" else -t["shares"] for t in txns)
            val = sum((t["shares"] if t["side"] == "A" else -t["shares"]) * (t["price"] or 0) for t in txns)
            out["insider"] = {"name": name, "position": position, "net_shares": net, "net_value": round(val, 2), "n": len(txns),
                              "first": min(t["date"] for t in txns), "side": "bought" if net > 0 else "sold" if net < 0 else "flat"}
    return out


def parse_buyback(html: str) -> Optional[dict]:
    """Form 9-1 Share Buy-Back Transactions (inside a 17-C cover)."""
    trades, cum = [], {}
    in_trades = False
    report_date = None
    for t in tables(html):
        for r in t:
            texts = [c["text"] for c in r]
            if len(texts) >= 2 and texts[0].startswith("Date of Report"):
                report_date = _date(texts[1])
            if texts[:3] == ["Date of Transaction", "Number of Shares Purchased", "Price Per Share"]:
                in_trades = True
                continue
            if in_trades:
                d = _date(texts[0]) if texts else None
                if d and len(texts) >= 3:
                    sh, px = num(texts[1]), num(texts[2])
                    if sh and px:
                        trades.append({"date": d, "shares": sh, "price": px})
                    continue
                in_trades = False
            if len(texts) >= 2:
                k = texts[0]
                if k.startswith("Cumulative Number of Shares Purchased"):
                    cum["cumulative"] = num(texts[1])
                elif k.startswith("Total Amount of Shares Repurchased"):
                    cum["total_repurchased"] = money(texts[1])
                elif k.startswith("Total Amount Appropriated"):
                    cum["appropriated"] = money(texts[1])
                elif k.startswith("Outstanding Shares") and len(texts) >= 3:
                    cum["outstanding_before"], cum["outstanding_after"] = num(texts[1]), num(texts[2])
                elif k.startswith("Treasury Shares") and len(texts) >= 3:
                    cum["treasury_before"], cum["treasury_after"] = num(texts[1]), num(texts[2])
    if not trades:
        return None
    shares = sum(x["shares"] for x in trades)
    value = sum(x["shares"] * x["price"] for x in trades)
    return {"report_date": report_date or max(x["date"] for x in trades), "first": min(x["date"] for x in trades),
            "last": max(x["date"] for x in trades), "shares": shares, "avg_price": round(value / shares, 4), "value": round(value, 2),
            "n_trades": len(trades), **cum}


# ── fetch ────────────────────────────────────────────────────────────────────

_last = [0.0]


def get(path: str, retries: int = 2) -> str:
    """GET one EDGE page, paced. Raises on anything but a normal page (a Cloudflare challenge counts as failure)."""
    import requests

    wait = PAUSE - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    err = None
    for i in range(retries + 1):
        try:
            r = requests.get(BASE + path, headers={"User-Agent": UA}, timeout=40)
            _last[0] = time.monotonic()
            if r.status_code == 200 and "Just a moment" not in r.text[:2000]:
                return r.text
            err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(PAUSE * (i + 2))
    raise RuntimeError(f"EDGE {path[:80]}: {err}")


def mdy(d: dt.date) -> str:
    return d.strftime("%m-%d-%Y")


def fetch_listing(kind: str, d_from: dt.date, d_to: dt.date, tmpl: str = "") -> list[dict]:
    """kind 'announcements' (all disclosures) or 'financialReports' (17-Q / 17-A). All pages."""
    out, p, n = [], 1, 1
    while p <= n:
        html = get(f"/{kind}/search.ax?keyword=&tmplNm={tmpl}&companyId=&fromDate={mdy(d_from)}&toDate={mdy(d_to)}&pageNo={p}")
        out += parse_listing(html)
        n = max(pages(html), 1)
        p += 1
    return out


def fetch_directory() -> list[dict]:
    out, p, n = [], 1, 1
    while p <= n:
        html = get(f"/companyDirectory/search.ax?companyId=&keyword=&sector=ALL&subsector=ALL&pageNo={p}")
        out += parse_directory(html)
        n = max(pages(html), 1)
        p += 1
    return out


def fetch_filing(edge_no: str) -> str:
    """The filing as HTML (two requests: the viewer names the file)."""
    fid = viewer_file_id(get(f"/openDiscViewer.do?edge_no={edge_no}"))
    if not fid:
        raise RuntimeError(f"no file id for {edge_no}")
    return get(f"/downloadHtml.do?file_id={fid}")


def post_json(path: str, payload: dict, retries: int = 2) -> dict:
    """POST a JSON body (the site's own chart endpoint) with the same pacing and checks as get()."""
    import requests

    wait = PAUSE - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    err = None
    for i in range(retries + 1):
        try:
            r = requests.post(BASE + path, json=payload, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}, timeout=40)
            _last[0] = time.monotonic()
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()
            err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(PAUSE * (i + 2))
    raise RuntimeError(f"EDGE POST {path}: {err}")


def parse_chart(js: dict) -> list[dict]:
    """/common/DisclosureCht.ax chartData -> [{date, open, high, low, close, value}] oldest first (value = pesos traded)."""
    out = []
    for b in js.get("chartData") or []:
        try:
            d = dt.datetime.strptime(b["CHART_DATE"], "%b %d, %Y %H:%M:%S").strftime("%Y-%m-%d")
            out.append({"date": d, "open": float(b["OPEN"]), "high": float(b["HIGH"]), "low": float(b["LOW"]), "close": float(b["CLOSE"]),
                        "value": float(b.get("VALUE") or 0.0)})
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["date"])
    return out


def fetch_chart(cmpy_id: str, security_id: str, start: dt.date, end: dt.date) -> list[dict]:
    return parse_chart(post_json("/common/DisclosureCht.ax", {"cmpy_id": cmpy_id, "security_id": security_id,
                                                              "startDate": mdy(start), "endDate": mdy(end)}))


def parse_stock_page(html: str) -> dict:
    """Outstanding shares, free float %, last price and the 52-week range from /companyPage/stockData.do."""
    cells = {}
    for t in tables(html):
        for r in t:
            texts = [c["text"] for c in r]
            for i in range(0, len(texts) - 1, 2):
                cells.setdefault(texts[i], texts[i + 1])
    pct = lambda v: num((v or "").replace("%", ""))
    return {"outstanding": num(cells.get("Outstanding Shares", "")), "free_float": pct(cells.get("Free Float Level(%)")),
            "last": num(cells.get("Last Traded Price", "")), "hi52": num(cells.get("52-Week High", "")), "lo52": num(cells.get("52-Week Low", ""))}


def link(edge_no: str) -> str:
    return f"{BASE}/openDiscViewer.do?edge_no={edge_no}"

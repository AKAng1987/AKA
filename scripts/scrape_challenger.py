"""
scrape_challenger.py -- Build a monthly history of Challenger, Gray &
Christmas announced job cuts from the firm's own report archive.

No free API carries this series. Each monthly report's lead sentence is
regular: "announced N job cuts in <Month>, up/down X% from the M cuts
announced in <PrevMonth>. It is up/down Y% from the K ... announced in
the same month last year." So each report yields three months, and every
month is cross-checked from up to three reports (its own, the next one's
"prior", the next year's "year-ago"). Conflicts are printed; the report's
own headline wins.

Also captures the AI-attributed cuts where the report states them
("AI has been cited in N job cut announcements" YTD; monthly where given).

Usage:
  .venv/bin/python scripts/scrape_challenger.py            # walk archive, write CSV
  PAGES=3 .venv/bin/python scripts/scrape_challenger.py    # limit category pages

Output: data_manual/challenger_job_cuts.csv (month, job_cuts, ai_cited_ytd,
source_url). Load into price-history with scripts/load_manual_series.py.
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time
import html as htmllib
from collections import defaultdict

import requests

BASE = "https://www.challengergray.com/blog/category/job-cuts-report/"
UA = {"User-Agent": "Mozilla/5.0 (CGI research; contact via repo)"}
MAX_PAGES = int(os.environ.get("PAGES", "12"))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_manual", "challenger_job_cuts.csv")

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}


def get(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    time.sleep(1.0)
    return r.text


def text_of(page: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", t)


def n(s: str) -> int:
    return int(s.replace(",", ""))


def report_links(page_html: str) -> list[str]:
    links = re.findall(r'href="(https://www\.challengergray\.com/blog/[^"]*challenger-report[^"]*)"', page_html)
    return list(dict.fromkeys(links))


def parse_report(url: str) -> dict:
    page = get(url)
    t = text_of(page)
    m = re.search(r'"datePublished":"(\d{4})-(\d{2})-(\d{2})', page)
    pub_y, pub_m = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    out = {"url": url, "published": f"{pub_y}-{pub_m:02d}" if pub_y else None, "points": {}, "ai_ytd": None, "ai_month": None}

    def ym_for(month_name: str, year_hint: int) -> str:
        mi = MONTHS[month_name.lower()]
        # a report published in Jan/Feb about December belongs to the prior year
        y = year_hint - 1 if (pub_m is not None and mi > pub_m and year_hint == pub_y) else year_hint
        return f"{y}-{mi:02d}"

    if pub_y is None:
        return out

    # current month
    cur = re.search(r"announced ([\d,]+) (?:job )?cuts in (January|February|March|April|May|June|July|August|September|October|November|December)", t)
    if cur:
        out["points"][ym_for(cur.group(2), pub_y)] = ("headline", n(cur.group(1)))
    # prior month
    prev = re.search(r"from the ([\d,]+) (?:job )?cuts announced in (January|February|March|April|May|June|July|August|September|October|November|December)", t)
    if prev and cur:
        pm = ym_for(prev.group(2), pub_y)
        # prior month is before the current one; if it wrapped past December, step the year back
        if pm > list(out["points"])[0]:
            pm = f"{int(pm[:4]) - 1}-{pm[5:]}"
        out["points"][pm] = ("prior", n(prev.group(1)))
    # same month last year
    yago = re.search(r"from the ([\d,]+) (?:layoff plans|job cuts|cuts|job cut announcements) announced in the same month (?:last year|a year ago|in \d{4})", t)
    if yago and cur:
        cur_ym = list(out["points"])[0]
        out["points"][f"{int(cur_ym[:4]) - 1}-{cur_ym[5:]}"] = ("yearago", n(yago.group(1)))
    # AI
    ai_ytd = re.search(r"(?:AI|Artificial Intelligence)[^.]{0,60}?cited in ([\d,]+) job cut", t)
    if ai_ytd:
        out["ai_ytd"] = n(ai_ytd.group(1))
    ai_m = re.search(r"(?:AI|Artificial Intelligence)[^.]{0,40}?(?:with|led|cited in|for) ([\d,]+) (?:cuts|job cuts)?(?:[^.]{0,40}?in (January|February|March|April|May|June|July|August|September|October|November|December))", t)
    if ai_m:
        out["ai_month"] = n(ai_m.group(1))
    return out


def main() -> None:
    seen, reports = set(), []
    for p in range(1, MAX_PAGES + 1):
        url = BASE if p == 1 else f"{BASE}page/{p}/"
        try:
            page = get(url)
        except requests.HTTPError as e:
            print(f"page {p}: {e.response.status_code} -- stopping")
            break
        links = [u for u in report_links(page) if u not in seen]
        if not links:
            print(f"page {p}: no new reports -- stopping")
            break
        print(f"page {p}: {len(links)} reports")
        for u in links:
            seen.add(u)
            try:
                r = parse_report(u)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {u}: {e}")
                continue
            reports.append(r)
            pts = ", ".join(f"{k}={v[1]:,}({v[0][0]})" for k, v in sorted(r["points"].items(), reverse=True))
            print(f"  {r['published']}  {pts}  ai_ytd={r['ai_ytd']}")

    # merge with precedence headline > prior > yearago; report conflicts
    rank = {"headline": 0, "prior": 1, "yearago": 2}
    by_month: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    ai_by_month: dict[str, int] = {}
    for r in reports:
        for ym, (kind, val) in r["points"].items():
            by_month[ym].append((rank[kind], val, r["url"]))
        if r["ai_ytd"] is not None and r["points"]:
            cur_ym = min(r["points"], key=lambda k: rank[r["points"][k][0]])
            ai_by_month[cur_ym] = r["ai_ytd"]
    rows = []
    for ym in sorted(by_month):
        cands = sorted(by_month[ym])
        best = cands[0]
        vals = {c[1] for c in cands}
        if len(vals) > 1:
            print(f"  conflict {ym}: {[(c[1], c[0]) for c in cands]} -> using {best[1]}")
        rows.append({"month": ym, "job_cuts": best[1], "ai_cited_ytd": ai_by_month.get(ym, ""), "source_url": best[2]})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["month", "job_cuts", "ai_cited_ytd", "source_url"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} months -> {OUT}  ({rows[0]['month']} .. {rows[-1]['month']})")


if __name__ == "__main__":
    main()

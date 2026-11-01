"""Watch the PSE for the results of an index review (PSEi and the PSE sector indices) and store them for the CGI Philippines tab.

The PSE publishes the results as a circular, a public PDF at documents.pse.com.ph/CircularOPSPDF/CN-YYYY-NNNN.pdf ("Results of the review of
PSE indices", with the full PSEi list). Each run tries the next WINDOW circular numbers after the last one found; a number that is not in that folder
answers 404 and costs one cheap request. A hit is read with macOS PDFKit (scripts/pdf_text.swift), and when it is an index review: the PSEi change
is the difference between the new member list and the stored one, each index's summary line is kept as written, and the stored PSEi member list
(which ph_prices_build.py uses for the PSEi flag) is replaced. Zero tokens. MSCI, FTSE and WisdomTree are not read here (their sites refuse scripts or
publish spreadsheets); those arrive through POST /api/ph/index_events on their announcement days.

    api/.venv/bin/python ~/market-dashboard/scripts/ph_index_watch.py            # dry run: print what it would store
    api/.venv/bin/python ~/market-dashboard/scripts/ph_index_watch.py --upload   # store to S3 (ph/index.json)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
import ph_edge as E  # noqa: E402

BASE = "https://documents.pse.com.ph/CircularOPSPDF"
WINDOW = 30
SEED_LAST = {"2026": 34}          # CN-2026-0035 (27 Jul 2026) is the newest review result known; the first run re-finds it


def url(year: int, n: int) -> str:
    return f"{BASE}/CN-{year}-{n:04d}.pdf"


def fetch_pdf(u: str) -> bytes | None:
    import requests

    E.get  # keep pacing helpers imported
    try:
        r = requests.get(u, headers={"User-Agent": E.UA}, timeout=40)
    except Exception:  # noqa: BLE001
        return None
    return r.content if r.status_code == 200 and r.content[:4] == b"%PDF" else None


def scan(state: dict, today: dt.date, log=print) -> dict:
    import time

    import ph_index as I

    state = dict(state or {})
    last = dict(state.get("last_scanned") or SEED_LAST)
    events = list(state.get("events") or [])
    members = list(state.get("psei_members") or [])
    years = [today.year] + ([today.year - 1] if today.month == 1 else [])
    for year in years:
        n0 = int(last.get(str(year), 0))
        top = n0
        seen = set((state.get("seen") or {}).get(str(year), []))
        for n in range(max(1, n0 - 7), n0 + 1 + WINDOW):       # the last few numbers again: a circular can appear a little after its number
            if n in seen:
                continue
            time.sleep(0.6)
            blob = fetch_pdf(url(year, n))
            if blob is None:
                continue
            top = max(top, n)
            seen.add(n)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(blob)
                path = f.name
            text = E.pdf_text(path)
            os.unlink(path)
            rv = I.parse_pse_review(text)
            log(f"CN-{year}-{n:04d}: {'index review ' + str(rv['date']) if rv else 'not an index review'}")
            if not rv:
                continue
            link = url(year, n)
            d = rv["date"] or today.isoformat()
            if rv["psei_members"] and len(rv["psei_members"]) >= 25:
                if members:
                    ch = I.psei_change(members, rv["psei_members"])
                    for kind in ("added", "removed"):
                        if ch[kind]:
                            events.append({"provider": "PSE", "date": d, "kind": "add" if kind == "added" else "delete", "index": "PSEi", "symbols": ch[kind],
                                           "effective": rv["effective"], "source_url": link, "note": f"CN-{rv['circular']}"})
                members = rv["psei_members"]
                state["psei_members_as_of"] = d
            for ln in rv["summary"]:
                events.append({"provider": "PSE", "date": d, "kind": "note", "index": ln.split(" ")[0] if ln.startswith("PSEi") else ln[:20],
                               "symbols": [], "effective": rv["effective"], "source_url": link, "note": ln[:140]})
            state["last_circular"] = {"circular": rv["circular"], "date": d, "effective": rv["effective"], "url": link}
        last[str(year)] = top
        state.setdefault("seen", {})[str(year)] = sorted(seen)[-60:]
    rows, _ = I.clean_events({"events": events}, today + dt.timedelta(days=0))   # revalidate; older than 90 days drop out of the window
    state.update({"events": I.merge_events(state.get("events") or [], [e for e in events if e in rows]) if rows else (state.get("events") or []),
                  "psei_members": members, "last_scanned": last, "scanned_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    import ph_data

    old = ph_data.read_blob("index") or {}
    state = scan(old, dt.date.today())
    print(f"last scanned {state['last_scanned']}; {len(state.get('events', []))} events; PSEi members {len(state.get('psei_members', []))}")
    if a.upload:
        ph_data.write_blob("index", state)
        print("stored ph/index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
release_trigger.py -- no-token trigger for the release-day read (handoff 07).

On the morning a release that can move a book lands (US: CPI, GDP, FOMC, SLOOS; PH: BSP, PH CPI/GDP) and he holds or plans a position
in that book, it writes a `release_before` event. The next morning's run writes `release_after` once. No position in that book: nothing.
The read itself is a Claude run (see ~/handoffs/runs/07_release_prompt.md), switched on only after Arvin approves its token cost.
State: out/release_state.json (what is waiting for its "after"). Events carry the release label and date only: no plan wording.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE = os.path.expanduser("~/market-dashboard/out/release_state.json")
AFTER_WINDOW_DAYS = 5


def triggers(today: dt.date, live: list[dict], fresh: dict | None, ph: dict | None, pending: list[dict]) -> tuple[list[dict], list[dict]]:
    """(events, new pending). Pure: positions_calendar.macro_rows supplies the dated releases per book."""
    from positions_calendar import macro_rows
    ev: list[dict] = []
    keep = [p for p in pending if p.get("date")]
    for book in ("PH", "US"):
        if not any((p.get("book") or "US") == book for p in live):
            continue
        for m in macro_rows(book, today, fresh, ph):
            if m["date"] != today:
                continue
            if not any(p["book"] == book and p["label"] == m["label"] and p["date"] == today.isoformat() for p in keep):
                keep.append({"book": book, "label": m["label"], "date": today.isoformat()})
                ev.append({"source": "release", "kind": "release_before", "level": "info", "symbol": None,
                           "text": f"{m['label']} lands today for the {book} book: the before-read is due",
                           "key": f"release_before:{book}:{m['label']}:{today.isoformat()}"})
    still = []
    for p in keep:
        d = dt.date.fromisoformat(p["date"])
        if d < today:
            if (today - d).days <= AFTER_WINDOW_DAYS:
                ev.append({"source": "release", "kind": "release_after", "level": "info", "symbol": None,
                           "text": f"{p['label']} landed {p['date']} for the {p['book']} book: the after-read is due",
                           "key": f"release_after:{p['book']}:{p['label']}:{p['date']}"})
        else:
            still.append(p)
    return ev, still


def run(today: dt.date, live: list[dict], fresh: dict | None, ph: dict | None, state_path: str = STATE) -> list[dict]:
    try:
        pending = json.load(open(state_path)).get("pending", [])
    except (OSError, ValueError):
        pending = []
    ev, new = triggers(today, live, fresh, ph, pending)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    json.dump({"pending": new}, open(state_path, "w"), indent=1)
    return ev

"""
thesis_trigger.py -- decides, with no tokens, whether a new Flow thesis entry needs the fact-check run (handoff 06).

A new entry of type day or week, longer than 150 words, filed in the last 3 days, not yet queued. It writes out/factcheck_queue.json
(entry ids and dates only, never his text) and one `thesis_new` event per entry in events.jsonl. It does not run the check itself:
that is a Claude run (see ~/handoffs/runs/06_factcheck_prompt.md), switched on only after Arvin approves its token cost.
Read-only on the mirror. An edited entry is not re-queued.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MIN_WORDS = 150
TYPES = ("day", "week")
FRESH_DAYS = 3
OUT = os.path.expanduser("~/market-dashboard/out")


def words(j: dict) -> int:
    t = j.get("text") or re.sub(r"<[^>]+>", " ", j.get("body") or "")
    return len(re.sub(r"&nbsp;|&[a-z]+;", " ", t).split())


def new_theses(journal: list[dict], queued: set[str], today: dt.date) -> list[dict]:
    out = []
    for j in journal:
        if j.get("type") not in TYPES or j.get("id") in queued or words(j) <= MIN_WORDS:
            continue
        try:
            filed = dt.date.fromisoformat(str(j.get("ts"))[:10])
        except ValueError:
            continue
        if (today - filed).days > FRESH_DAYS or filed > today:
            continue
        out.append({"id": j["id"], "entry_date": j.get("entry_date") or str(j["ts"])[:10], "type": j["type"], "words": words(j)})
    return sorted(out, key=lambda x: x["id"])


def run(mirror: str = os.path.expanduser("~/pulse/mirror"), out_dir: str = OUT, today: dt.date | None = None, log=True) -> list[dict]:
    today = today or dt.date.today()
    journal = []
    for f in sorted(glob.glob(os.path.join(mirror, "journal", "*.json"))):
        try:
            journal.append(json.load(open(f)))
        except ValueError:
            continue
    state_f = os.path.join(out_dir, "factcheck_state.json")
    try:
        queued = set(json.load(open(state_f)).get("queued", []))
    except (OSError, ValueError):
        queued = set()
    fresh = new_theses(journal, queued, today)
    if not fresh:
        return []
    os.makedirs(out_dir, exist_ok=True)
    try:
        pending = json.load(open(os.path.join(out_dir, "factcheck_queue.json")))
    except (OSError, ValueError):
        pending = []
    open(os.path.join(out_dir, "factcheck_queue.json"), "w").write(json.dumps(pending + fresh, indent=1))
    open(state_f, "w").write(json.dumps({"queued": sorted(queued | {e["id"] for e in fresh})}, indent=1))
    if log:
        import events as EV
        for e in fresh:
            EV.log_event("thesis", "thesis_new", "info", f"New Flow {e['type']} entry for {e['entry_date']} ({e['words']} words) is waiting for the fact-check",
                         key=f"thesis_new:{e['id']}")
    return fresh


if __name__ == "__main__":
    r = run()
    print(f"{len(r)} new thesis entr{'y' if len(r) == 1 else 'ies'} queued")

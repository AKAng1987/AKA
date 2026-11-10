"""
events.py -- one append-only log of what happened, so the morning screen and the brain read one file.

  ~/market-dashboard/out/events.jsonl, one JSON line per event:
    ts (UTC ISO), source (job), kind, level (info|warn|alert), symbol (optional), text (one plain sentence), key

A job imports log_event and calls it with data it already has. The key de-duplicates: an event whose key is already in the
last DEDUPE_LINES lines is not written again, so running a job twice adds nothing. A condition that persists (a stale mirror)
uses a key ending in the date so it reappears each day until it is fixed.

Rules: text is built from fixed sentences and capped at 200 characters; never Flow text, plan "wrong" lines, balances or
account ids. Long digit runs are scrubbed anyway. Logging never raises: a failure here must not break the job that calls it.
Plain stdlib; zero tokens; no network.

Set EVENTS_FILE to point somewhere else (tests do).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re

DEFAULT_PATH = os.path.expanduser("~/market-dashboard/out/events.jsonl")
DEDUPE_LINES = 2000
MAX_TEXT = 200
LEVELS = {"info": 0, "warn": 1, "alert": 2}
# data-health sources lead the screen: a stale mirror or bad data makes everything below it suspect
DATA_SOURCES = {"mirror", "cgi", "cgi_health", "task_watch", "ph_daily"}
KEEP_DAYS = 120


def path() -> str:
    return os.environ.get("EVENTS_FILE") or DEFAULT_PATH


def _clean(text: str) -> str:
    text = re.sub(r"\b\d{6,}\b", "[number]", " ".join(str(text).split()))
    return text[: MAX_TEXT - 1] + "…" if len(text) > MAX_TEXT else text


def _tail(f, n: int) -> list[str]:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - n * 400))
    return f.read().decode("utf-8", "ignore").splitlines()[-n:]


def log_event(source: str, kind: str, level: str, text: str, symbol: str | None = None, key: str | None = None,
              ts: dt.datetime | None = None, file: str | None = None) -> bool:
    """Append one event. Returns True if written, False if it was a duplicate or logging failed. Never raises."""
    try:
        if level not in LEVELS:
            level = "info"
        text = _clean(text)
        key = key or f"{source}:{kind}:{text}"
        p = file or path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                for line in _tail(f, DEDUPE_LINES):
                    try:
                        if json.loads(line).get("key") == key:
                            return False
                    except ValueError:
                        continue
                when = (ts or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
                rec = {"ts": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source, "kind": kind, "level": level,
                       "symbol": symbol, "text": text, "key": key}
                f.seek(0, 2)
                f.write((json.dumps(rec, ensure_ascii=False) + "\n").encode())
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception:  # noqa: BLE001 -- logging must never break a job
        return False


def read_events(since: dt.datetime, file: str | None = None) -> list[dict]:
    """Events with ts >= since (aware datetime), oldest first. Missing or unreadable file gives []."""
    out = []
    try:
        for line in open(file or path(), encoding="utf-8"):
            try:
                r = json.loads(line)
                if dt.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) >= since:
                    out.append(r)
            except (ValueError, KeyError):
                continue
    except OSError:
        return []
    return out


def screen_order(events: list[dict]) -> list[dict]:
    """Alerts before warns; within a level, data-health sources first, then newest first. Info is dropped."""
    synced = max((e["ts"] for e in events if e.get("source") == "mirror" and e.get("kind") == "synced"), default="")
    # a stale-mirror alert is cleared by a later "mirror synced" event; other events are history and stay for the window
    ev = [e for e in events if e.get("level") in ("warn", "alert")
          and not (e.get("kind") in ("stale", "mirror_old", "no_sync") and e.get("source") in ("mirror", "task_watch") and e["ts"] < synced)]
    ev.sort(key=lambda e: e["ts"], reverse=True)
    ev.sort(key=lambda e: (-LEVELS[e["level"]], 0 if e.get("source") in DATA_SOURCES else 1))
    return ev


def rotate(keep_days: int = KEEP_DAYS, file: str | None = None, now: dt.datetime | None = None) -> int:
    """Move events older than keep_days to events_archive/events-YYYY-MM.jsonl beside the log. Returns lines moved."""
    p = file or path()
    try:
        now = now or dt.datetime.now(dt.timezone.utc)
        cut = now - dt.timedelta(days=keep_days)
        keep, old = [], {}
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
                when = dt.datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                keep.append(line)
                continue
            if when < cut:
                old.setdefault(r["ts"][:7], []).append(line)
            else:
                keep.append(line)
        if not old:
            return 0
        adir = os.path.join(os.path.dirname(p), "events_archive")
        os.makedirs(adir, exist_ok=True)
        for month, lines in old.items():
            with open(os.path.join(adir, f"events-{month}.jsonl"), "a", encoding="utf-8") as g:
                g.writelines(lines)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as g:
            g.writelines(keep)
        os.replace(tmp, p)
        return sum(len(v) for v in old.values())
    except Exception:  # noqa: BLE001
        return 0

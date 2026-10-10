"""
yt_scan.py -- zero-token first pass for Video intake: which new videos are worth a model's attention.

Reads config/yt_sources.json (channels, kind, window), pulls each channel's free RSS feed (last 15 uploads),
keeps videos inside the kind's window that it has not seen before (~/.cgi/yt_seen.json), fetches the transcript
(youtube-transcript-api, no key) and scores it: mentions of instruments CGI tracks, mentions of macro releases,
and rule-like language ("when X ... tends to", "historically", "every time"). Prints a JSON shortlist, best first.
The model then reads only the top few transcripts (/video-to-study scan mode).

  python3 scripts/yt_scan.py                 # all kinds, shortlist to stdout, marks shortlisted videos seen
  python3 scripts/yt_scan.py --kind live_call --top 5
  python3 scripts/yt_scan.py --due --top 5      # scheduled run: live calls Mon/Thu, rules Sat, background monthly
  python3 scripts/yt_scan.py --channels 42Macro,MarketsUnscripted --dry-run   # test; does not mark seen

Needs youtube-transcript-api: it re-runs itself inside ~/.cgi/ytvenv if that exists (create with
`python3 -m venv ~/.cgi/ytvenv && ~/.cgi/ytvenv/bin/pip install youtube-transcript-api`).
"""
from __future__ import annotations

import argparse, datetime as dt, json, os, pathlib, re, sys, time, urllib.error, urllib.request, xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = pathlib.Path.home() / ".cgi"
VENV_PY = STATE / "ytvenv" / "bin" / "python"
# Compare the environment, not the executable: a venv's python is a symlink to the system one.
if os.environ.get("YT_SCAN_VENV") != "1" and VENV_PY.exists() and pathlib.Path(sys.prefix) != STATE / "ytvenv":
    os.environ["YT_SCAN_VENV"] = "1"
    os.execv(str(VENV_PY), [str(VENV_PY), __file__, *sys.argv[1:]])

UA = {"User-Agent": "Mozilla/5.0"}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
RULE = re.compile(r"\b(when(ever)? .{0,60}\b(tends? to|usually|typically|historically|always)|every time|historically|base rate|"
                  r"back-?test|on average|win rate|hit rate|the signal|the setup|the rule)\b", re.I)
MACRO = re.compile(r"\b(CPI|PCE|GDP|payrolls?|FOMC|the Fed|yields?|treasur(y|ies)|liquidity|credit spreads?|dollar|DXY|"
                   r"oil|gold|copper|recession|inflation|rate (cut|hike)s?|curve|QT|QE|SLOOS|ISM|unemployment)\b", re.I)


def _get(url: str, timeout: int = 30, tries: int = 3) -> str:
    """YouTube throttles bursts (2026-10-10: 8 of 9 feeds failed in one scan), so retry with back-off."""
    last: Exception | None = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504):
                break                       # 404 and the like will not improve by waiting
        except Exception as exc:  # noqa: BLE001 -- timeouts, resets
            last = exc
        time.sleep(4 * (i + 1))
    raise last


def channel_id(handle: str, cache: dict) -> str | None:
    if handle in cache:
        return cache[handle]
    try:
        html = _get(f"https://www.youtube.com/@{handle}")
    except Exception:
        return None
    m = re.search(r'"externalId":"(UC[\w-]{22})"', html) or re.search(r'channel_id=(UC[\w-]{22})', html)
    cache[handle] = m.group(1) if m else None
    return cache[handle]


def uploads(cid: str) -> list[dict]:
    xml = _get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", NS):
        out.append({"id": e.find("yt:videoId", NS).text, "title": e.find("a:title", NS).text,
                    "published": e.find("a:published", NS).text[:10],
                    "url": f"https://www.youtube.com/watch?v={e.find('yt:videoId', NS).text}"})
    return out


LAST_ERR: dict[str, str] = {}


def transcript(vid: str) -> str | None:
    """YouTube throttles quick successive transcript requests, so pace them and retry once."""
    import time
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        LAST_ERR[vid] = "youtube-transcript-api not installed (see module docstring)"
        return None
    for attempt in (1, 2):
        time.sleep(2.0 if attempt == 1 else 8.0)
        try:
            return " ".join(s.text for s in YouTubeTranscriptApi().fetch(vid))
        except Exception as exc:
            LAST_ERR[vid] = type(exc).__name__
    return None


def universe() -> set[str]:
    """Tickers CGI tracks, from the TradingView symbol table in the CGI repo (if checked out beside this one)."""
    p = pathlib.Path.home() / "cgi-vercel" / "api" / "tv_symbols.json"
    try:
        return {k for k in json.loads(p.read_text()) if len(k) >= 3}
    except Exception:
        return set()


def watch_symbols() -> set[str]:
    """Arvin's TradingView watchlist symbols, saved locally by the /video-to-study scan step
    (~/.cgi/yt_watch_symbols.json: a list of tickers). Kept out of the repo: personal lists, public repo."""
    p = STATE / "yt_watch_symbols.json"
    try:
        return {s.split(":")[-1] for s in json.loads(p.read_text()) if not str(s).startswith("###")}
    except Exception:
        return set()


def due_kinds(today: dt.date) -> list[str]:
    """Schedule: live calls Mon+Thu, rules/methods Sat, background + dormant check on the first Sat of the month."""
    k = []
    if today.weekday() in (0, 3):
        k.append("live_call")
    if today.weekday() == 5:
        k += ["rule", "method"]
        if today.day <= 7:
            k += ["background", "dormant"]
    return k


def score(text: str, tickers: set[str], watch: set[str] = frozenset()) -> dict:
    words = re.findall(r"\b[A-Z]{2,6}\b", text)
    hits = sorted({w for w in words if w in tickers and len(w) >= 3})
    whits = sorted({w for w in words if w in watch})
    return {"rule_phrases": len(RULE.findall(text)), "macro_terms": len(MACRO.findall(text)),
            "tickers": hits[:15], "watch_hits": whits[:15], "chars": len(text)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind"); ap.add_argument("--channels"); ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--due", action="store_true", help="scan only the kinds scheduled for today")
    a = ap.parse_args()
    cfg = json.loads((ROOT / "config" / "yt_sources.json").read_text())
    STATE.mkdir(exist_ok=True)
    seen_p, ids_p = STATE / "yt_seen.json", STATE / "yt_channel_ids.json"
    seen = set(json.loads(seen_p.read_text())) if seen_p.exists() else set()
    ids = json.loads(ids_p.read_text()) if ids_p.exists() else {}
    tickers, watch, today = universe(), watch_symbols(), dt.date.today()
    kinds = due_kinds(today) if a.due else ([a.kind] if a.kind else None)
    if a.due and not kinds:
        print(json.dumps({"due": [], "note": f"nothing scheduled on {today:%A}"})); return 0
    chans = [c for c in cfg["channels"] if (kinds is None and c["kind"] != "dormant") or (kinds and c["kind"] in kinds)]
    chans = [c for c in chans if not a.channels or c["handle"] in a.channels.split(",")]
    rows, problems, revived = [], [], []
    for c in chans:
        if c["kind"] == "dormant":   # existence check only: has it posted in the last 31 days?
            cid = channel_id(c["handle"], ids)
            try:
                v = uploads(cid)[:1] if cid else []
            except Exception:
                v = []
            if v and (today - dt.date.fromisoformat(v[0]["published"])).days <= 31:
                revived.append(f"{c['handle']}: posted {v[0]['published']} - {v[0]['title']}")
            continue
        time.sleep(1.5)                     # pace the channels; bursts are what get throttled
        cid = channel_id(c["handle"], ids)
        if not cid:
            problems.append(f"{c['handle']}: channel id not found"); continue
        try:
            vids = uploads(cid)
        except Exception as exc:
            problems.append(f"{c['handle']}: feed error {type(exc).__name__} {getattr(exc, 'code', '')}".strip()); continue
        win = cfg["windows_days"][c["kind"]]
        for v in vids:
            age = (today - dt.date.fromisoformat(v["published"])).days
            if age > win or v["id"] in seen:
                continue
            t = transcript(v["id"])
            s = score(t, tickers, watch) if t else {"rule_phrases": 0, "macro_terms": 0, "tickers": [], "watch_hits": [], "chars": 0}
            pts = (s["rule_phrases"] * 3 + min(s["macro_terms"], 40) + 2 * len(s["tickers"])
                   + 4 * len(s["watch_hits"])) if t else -1
            rows.append({**v, "channel": c["handle"], "kind": c["kind"], "age_days": age,
                         "has_transcript": bool(t), "score": pts, **s})
    rows.sort(key=lambda r: (-r["score"], r["age_days"]))
    short = [r for r in rows if r["has_transcript"]][: a.top]
    ids_p.write_text(json.dumps(ids, indent=1))
    if not a.dry_run:
        seen |= {r["id"] for r in short}
        seen_p.write_text(json.dumps(sorted(seen)))
    print(json.dumps({"due": kinds, "watch_symbols": len(watch), "dormant_revived": revived,
                      "scanned_channels": len(chans), "new_videos": len(rows),
                      "no_transcript": [f"{r['channel']}: {r['title']} ({LAST_ERR.get(r['id'], '?')})" for r in rows if not r["has_transcript"]][:20],
                      "problems": problems, "shortlist": short}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

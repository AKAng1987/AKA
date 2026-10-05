"""
cgi-cts-poster -- posts CGI regime and theme changes to the CTS Ideas feed,
with no Claude in the loop.

WHY THIS IS A LAMBDA AND NOT A CLAUDE ROUTINE
The refresh routine died on 2026-10-01 when the token budget did, and nothing
said so. A regime-shift alert that goes quiet exactly when the budget runs out
is the failure it exists to prevent. The schedule lives in EventBridge, the post
is a template over facts CGI already computed, and no model is involved.

WHAT IT POSTS (one post per event, never re-posted)
  regime flip     the new regime, what flipped, and its best / worst tickers
  theme onset     a theme's RS run started
  theme end       a theme's leg lost its trend
cgi_changes.RATES marks theme_end as not push-worthy for the *brief*; the
poster has its own list because a feed read by traders wants to hear a theme
ended. That is a reading preference, not a change to the global rule.

NO CGI SECRET NEEDED
It reads the same public proxies the TradingView routine does
(/api/brief, /api/watchlists on Vercel). The only secret is the CTS agent
token, kept in Secrets Manager or the function's own environment.

DE-DUP, AND WHY IT FAILS SAFE
Posting to ~40 traders under a bot's name makes a duplicate visible, unlike a
duplicate email. State lives in S3 (State/cts_alerts_posted.json):
  * an event is recorded only AFTER the post succeeds, so a failed post retries
    on the next run instead of being lost;
  * a missing or unreadable state blob means "no prior state" and the run
    SEEDS the current events without posting -- otherwise the first run after
    any outage would fire everything at once. Same rule as cgi_state.py.

TWO GUARDS AGAINST A WRONG POST
  * /api/watchlists is cached for up to 34h. Right after a flip it can still
    show the OLD regime's best/worst. A regime post is deferred unless the
    watchlists' current regime equals the brief's.
  * DRY_RUN defaults ON. It prints exactly what it would send and posts nothing
    until DRY_RUN=0 is set deliberately.

A failed or refused post is a side effect, never the main deliverable: the
function logs it and carries on.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

BRIEF_URL = os.environ.get("BRIEF_URL", "https://cgi-vercel.vercel.app/api/brief?cadence=daily")
WEEKLY_BRIEF_URL = os.environ.get("WEEKLY_BRIEF_URL", "https://cgi-vercel.vercel.app/api/brief?cadence=weekly")
WATCHLISTS_URL = os.environ.get("WATCHLISTS_URL", "https://cgi-vercel.vercel.app/api/watchlists")
# Needed for LIVE posting only (DRY_RUN needs nothing). Deliberately NO default: the address of
# the CTS Ideas service is internal to the user's employer and this repository is public. Set it
# in the Lambda console next to CTS_AGENT_TOKEN; the value is in the CTS connection doc.
CTS_MCP_URL = os.environ.get("CTS_MCP_URL", "")
_NO_URL = ("CTS_MCP_URL is not set on this function. Set it in the Lambda console next to "
           "CTS_AGENT_TOKEN; the value is in the CTS Ideas connection doc and is never committed.")
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "3"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))

STATE_BUCKET = os.environ.get(
    "STATE_BUCKET", "cmon-stage-backend-369568916817-ap-southeast-1-reports")
STATE_KEY = os.environ.get("STATE_KEY", "State/cts_alerts_posted.json")
KEEP_DAYS = 30

REGIME_KINDS = {"regime_flip_compass", "regime_flip_grid"}
THEME_KINDS = {"theme_onset", "theme_end"}
ALERT_KINDS = REGIME_KINDS | THEME_KINDS

SOFT_CAP = 600          # AGENT-PROMPT-GUIDE: one condensed paragraph

# The post links to the app and carries ONE picture, the regime card, drawn by the app at /share/regime.png.
# CTS's own server fetches the picture from this URL (20s timeout) and re-hosts it, so it must be public.
APP_URL = os.environ.get("APP_URL", "https://cgi-vercel.vercel.app").rstrip("/")
REGIME_IMAGE_PATH = "/share/regime.png"
IMAGE_MAX_BYTES = 5 * 1024 * 1024     # the CTS limit
IMAGE_TIMEOUT = 25
NOW_LIST_NAME = "CGI · now"
PROTOCOL = "2025-03-26"


# ── pure functions (unit-tested without any network) ────────────────────────

def event_key(c: dict) -> str:
    return f"{c.get('kind')}|{c.get('title')}|{c.get('when')}"


def select_events(changes: list[dict], posted: set[str]) -> list[dict]:
    """Alert-worthy events not yet posted, rarest first, deduped within the run."""
    seen, out = set(), []
    for c in sorted(changes, key=lambda c: -(c.get("rarity") or 0.0)):
        if c.get("kind") not in ALERT_KINDS:
            continue
        k = event_key(c)
        if k in posted or k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def regimes_agree(brief: dict, watchlists: dict) -> bool:
    try:
        b = brief["sections"][0]["body"]["current"]
        w = watchlists["current"]
        return (b["compass"], b["grid"]) == (w["compass"], w["grid"])
    except (KeyError, IndexError, TypeError):
        return False


def _now_list(watchlists: dict) -> dict | None:
    return next((w for w in watchlists.get("watchlists", []) if w.get("name") == "CGI · now"), None)


def _plain(detail: str) -> str:
    """'grid 3 -> 2' (or 'grid_US 3 -> 2') is a model name; readers know it as G3 -> G2.

    The live brief writes 'grid 3 -> 2' with no _US; an earlier version of this only matched
    the _US form because the test fixture was written from an assumption, not from real data.
    """
    return re.sub(r"\b(compass|grid)(?:_US)?\s+(\d)\s*->\s*(\d)",
                  lambda m: f"{m.group(1)[0].upper()}{m.group(2)} → {m.group(1)[0].upper()}{m.group(3)}",
                  detail or "")


def tv_list_url(watchlists: dict, name: str = NOW_LIST_NAME) -> str | None:
    """Public TradingView link to a CGI list, built from the id the API serves, never typed."""
    w = next((x for x in watchlists.get("watchlists", []) if x.get("name") == name), None)
    return f"https://www.tradingview.com/watchlists/{w['watchlist_id']}/" if w and w.get("watchlist_id") else None


def _links(tv_url: str | None) -> list[str]:
    lines = []
    if tv_url:
        lines.append(f"Best / worst 20 for this regime (TradingView): {tv_url}")
    lines.append(f"CGI app: {APP_URL}")
    return lines


def compose_regime(c: dict, wl_now: dict, date: str, tv_url: str | None = None) -> dict:
    """Regime-flip post: what flipped and the new regime, the regime card as a picture, and links.

    The best/worst 20 used to be listed in the text. They now live in the TradingView list the
    post links to, so the text stays short and the list cannot go stale inside a post.
    """
    regime = wl_now.get("regime", "?")
    when = f" ({c['when']})" if c.get("when") else ""
    head = (f"Regime shift{when}: {c['title']}. CGI now reads {regime}. {_plain(c.get('detail', ''))}".rstrip(". ")
            + ".")
    return {"text": "\n".join([head] + _links(tv_url))[:SOFT_CAP], "tickers": [],
            "tags": ["regime", "macro"], "runId": f"cgi-{date}",
            "images": [{"url": f"{APP_URL}{REGIME_IMAGE_PATH}?v={date}",
                        "caption": f"CGI regime card, {regime}, as of {date}"}]}


def compose_theme(c: dict, date: str, tv_url: str | None = None) -> dict:
    head = f"Theme change: {c['title']}. {c.get('detail', '')}".rstrip(". ") + "."
    return {"text": "\n".join([head] + _links(tv_url))[:SOFT_CAP], "tickers": [],
            "tags": ["themes", "macro"], "runId": f"cgi-{date}"}


def verify_image(img: dict) -> bool:
    """Is this picture safe to hand to CTS? 200, a real PNG, under their 5MB limit.

    CTS fetches the URL itself with a 20s timeout. Fetching it here first also wakes the app and
    the API and primes the cache, so their fetch finds it warm. A picture that fails is simply
    left off: the post still goes out with its text and links.
    """
    try:
        req = urllib.request.Request(img["url"], headers={"User-Agent": "cgi-cts-poster"})
        with urllib.request.urlopen(req, timeout=IMAGE_TIMEOUT) as r:
            status = r.status
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            body = r.read(IMAGE_MAX_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        print(f"[image] unusable, posting without it: {type(exc).__name__}: {str(exc)[:120]}")
        return False
    ok = (status == 200 and ctype == "image/png" and 0 < len(body) <= IMAGE_MAX_BYTES
          and body[:8] == b"\x89PNG\r\n\x1a\n")
    if not ok:
        print(f"[image] unusable, posting without it: status={status} type={ctype} bytes={len(body)}")
    return ok


def attach_images(idea: dict) -> dict:
    """Keep only the pictures that verify; drop the key when none do."""
    good = [i for i in idea.get("images", []) if verify_image(i)]
    if good:
        idea["images"] = good
    else:
        idea.pop("images", None)
    return idea


def prune(posted: dict[str, str], today: dt.date) -> dict[str, str]:
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    return {k: v for k, v in posted.items() if v >= cutoff}


# ── I/O ─────────────────────────────────────────────────────────────────────

def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "cgi-cts-poster"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _s3():
    import boto3
    return boto3.client("s3")


class StateUnreadable(Exception):
    """The state blob could not be read for a reason other than not existing."""


def load_state() -> dict[str, str] | None:
    """{event_key: date_posted}, or None meaning there is genuinely no state.

    ONLY a missing object is "no prior state". Anything else -- a permissions
    error, an outage, a corrupt blob -- raises. Treating every failure as a
    first run would re-seed and OVERWRITE the real record, and post nothing,
    so a broken read would look like a quiet day indefinitely. (S3 reports a
    missing key as AccessDenied unless the role may list the bucket, which is
    why the role is granted ListBucket on this prefix.)
    """
    from botocore.exceptions import ClientError
    try:
        obj = _s3().get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            print("[state] no prior state (NoSuchKey)")
            return None
        raise StateUnreadable(f"cannot read {STATE_KEY}: {exc}") from exc
    try:
        return dict(json.loads(obj["Body"].read().decode("utf-8")).get("posted", {}))
    except (ValueError, AttributeError) as exc:
        raise StateUnreadable(f"{STATE_KEY} is not valid state: {exc}") from exc


def save_state(posted: dict[str, str]) -> None:
    _s3().put_object(Bucket=STATE_BUCKET, Key=STATE_KEY,
                     Body=json.dumps({"posted": posted}).encode("utf-8"),
                     ContentType="application/json")


def _token() -> str:
    t = os.environ.get("CTS_AGENT_TOKEN")
    if t:
        return t
    sid = os.environ.get("CTS_SECRET_ID")
    if sid:
        import boto3
        return boto3.client("secretsmanager").get_secret_value(SecretId=sid)["SecretString"].strip()
    raise RuntimeError("no CTS agent token: set CTS_AGENT_TOKEN or CTS_SECRET_ID. "
                       "Mint one in the CTS app (Admin -> Agents -> + New).")


def _rpc(token: str, payload: dict, session: str | None = None) -> tuple[dict | None, str | None]:
    if not CTS_MCP_URL:
        raise RuntimeError(_NO_URL)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(CTS_MCP_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        sid = r.headers.get("Mcp-Session-Id") or session
        raw = r.read().decode("utf-8")
    if not raw.strip():
        return None, sid
    if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
        # Streamable HTTP may answer as a one-event SSE stream.
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip()), sid
        return None, sid
    return json.loads(raw), sid


class CtsError(Exception):
    pass


def post_idea(token: str, idea: dict) -> dict:
    """initialize -> initialized -> tools/call post_idea. Raises CtsError on a
    refusal (daily limit, bad field) so the caller can stop cleanly."""
    _, sid = _rpc(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                     "clientInfo": {"name": "cgi-cts-poster", "version": "1"}}})
    _rpc(token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    resp, _ = _rpc(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "post_idea", "arguments": idea}}, sid)
    if not resp or "error" in resp:
        raise CtsError(json.dumps((resp or {}).get("error", "empty response")))
    result = resp.get("result", {})
    if result.get("isError"):
        raise CtsError(json.dumps(result.get("content")))
    return result


# ── handler ─────────────────────────────────────────────────────────────────

def run() -> dict:
    if not DRY_RUN and not CTS_MCP_URL:
        # Fail the whole run, loudly, before touching state: a configuration error must not
        # look like one "failed, will retry" line per event.
        raise RuntimeError(_NO_URL)
    today = dt.datetime.now(dt.timezone.utc).date()
    date = today.isoformat()
    brief = _get_json(BRIEF_URL)
    watchlists = _get_json(WATCHLISTS_URL)

    state = load_state()
    candidates = [c for c in brief.get("changes", []) if c.get("kind") in ALERT_KINDS]

    if state is None:
        # First run, or state lost: record what is already there, post nothing.
        seeded = {event_key(c): date for c in candidates}
        if not DRY_RUN:
            save_state(seeded)
        print(f"[seed] no prior state; recorded {len(seeded)} current events, posted none")
        return {"seeded": len(seeded), "posted": 0, "dry_run": DRY_RUN}

    posted = prune(state, today)
    todo = select_events(candidates, set(posted))
    wl_now = _now_list(watchlists)
    tv_url = tv_list_url(watchlists)
    agree = regimes_agree(brief, watchlists)

    sent, deferred, failed = [], [], []
    for c in todo:
        if len(sent) >= MAX_POSTS_PER_RUN:
            deferred.append((event_key(c), "per-run cap"))
            continue
        if c["kind"] in REGIME_KINDS:
            if wl_now is None or not agree:
                deferred.append((event_key(c), "watchlists still show the previous regime"))
                continue
            idea = attach_images(compose_regime(c, wl_now, date, tv_url))
        else:
            idea = compose_theme(c, date, tv_url)

        if DRY_RUN:
            print(f"[dry-run] WOULD POST ({len(idea['text'])} chars): {json.dumps(idea, ensure_ascii=False)}")
            sent.append(event_key(c))
            continue
        try:
            post_idea(_token(), idea)
        except Exception as exc:  # noqa: BLE001
            print(f"[post] failed, will retry next run: {exc}")
            failed.append(event_key(c))
            if "limit" in str(exc).lower():
                break
            continue
        posted[event_key(c)] = date
        sent.append(event_key(c))
        save_state(posted)       # after EACH success: a crash mid-run cannot double-post

    out = {"posted": len(sent), "deferred": deferred, "failed": failed,
           "candidates": len(candidates), "dry_run": DRY_RUN}
    print(json.dumps(out))
    return out


def selftest() -> dict:
    """Read-only connection check, run with the function's OWN environment.

    Invoke with {"selftest": true}. It signs in and calls `whoami`, which consumes no quota
    and posts nothing, so the CTS token and address never have to pass through anyone's hands:
    set them in the Lambda console, run this, and read the result. Nothing returned here
    contains the token.
    """
    missing = [n for n, v in (("CTS_MCP_URL", CTS_MCP_URL),
                              ("CTS_AGENT_TOKEN or CTS_SECRET_ID",
                               os.environ.get("CTS_AGENT_TOKEN") or os.environ.get("CTS_SECRET_ID"))) if not v]
    if missing:
        return {"ok": False, "missing": missing}
    try:
        token = _token()
        _, sid = _rpc(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": "cgi-cts-poster", "version": "1"}}})
        _rpc(token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
        resp, _ = _rpc(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "whoami", "arguments": {}}}, sid)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}", "hint": "401 means the token is wrong, disabled or rotated"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if not resp or "error" in resp or (resp.get("result") or {}).get("isError"):
        return {"ok": False, "error": json.dumps((resp or {}).get("error") or (resp or {}).get("result"))[:300]}
    return {"ok": True, "dry_run": DRY_RUN, "whoami": resp.get("result")}


def replay(spec: dict, preview: bool, repost: bool = False) -> dict:
    """Post ONE named recent event on demand, e.g. {"kind": "regime_flip_grid", "when": "2026-09-30"}.

    For an event the daily run could not have seen (it only looks back a day) or to prove the
    post path against the real server. Looks the event up in the WEEKLY brief, so it posts what
    CGI actually recorded, never text supplied by hand. Guards:
      * only alert kinds, and the event must exist exactly once;
      * a regime post is refused if a LATER regime flip exists or the watchlists still show another
        regime, because "CGI now reads ..." and the best/worst lists would then be wrong;
      * already posted (per the same de-dup record the daily run uses) -> not posted twice, UNLESS
        repost=True, which bypasses only this check (used once to check a new post format; it
        re-records the event, so the daily run cannot post it either);
      * preview=True (or DRY_RUN) prints what it would send and writes nothing.
    """
    kind, when = spec.get("kind"), spec.get("when")
    if kind not in ALERT_KINDS or not when:
        return {"ok": False, "error": f"replay needs kind in {sorted(ALERT_KINDS)} and when=YYYY-MM-DD"}
    brief, watchlists = _get_json(WEEKLY_BRIEF_URL), _get_json(WATCHLISTS_URL)
    changes = brief.get("changes", [])
    matches = [c for c in changes if c.get("kind") == kind and c.get("when") == when]
    if len(matches) != 1:
        return {"ok": False, "error": f"expected exactly one {kind} on {when} in the weekly brief, found {len(matches)}"}
    c = matches[0]
    date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if kind in REGIME_KINDS:
        later = [x["when"] for x in changes if x.get("kind") in REGIME_KINDS and (x.get("when") or "") > when]
        wl_now = _now_list(watchlists)
        if later:
            return {"ok": False, "error": f"a later regime flip exists ({later}); this one is no longer the current regime"}
        if wl_now is None or not regimes_agree(brief, watchlists):
            return {"ok": False, "error": "watchlists still show a different regime than the brief; try again later"}
        idea = attach_images(compose_regime(c, wl_now, date, tv_list_url(watchlists)))
    else:
        idea = compose_theme(c, date, tv_list_url(watchlists))
    idea["tags"] = idea["tags"] + ["replay"]
    key = event_key(c)
    state = load_state() or {}
    if key in state and not repost:
        return {"ok": True, "already_posted": True, "key": key}
    if preview or DRY_RUN:
        return {"ok": True, "previewed": True, "key": key, "chars": len(idea["text"]), "idea": idea}
    if not CTS_MCP_URL:
        raise RuntimeError(_NO_URL)
    result = post_idea(_token(), idea)
    state[key] = date
    save_state(state)
    return {"ok": True, "posted": True, "key": key, "result": result}


def lambda_handler(event, context):  # noqa: ANN001
    if isinstance(event, dict) and event.get("selftest"):
        return selftest()
    if isinstance(event, dict) and event.get("replay"):
        return replay(event["replay"], preview=bool(event.get("preview")), repost=bool(event.get("repost")))
    return run()


if __name__ == "__main__":
    run()

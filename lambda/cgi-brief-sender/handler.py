"""
cgi-brief-sender -- emails the CGI morning brief, with no Claude in the loop.

WHY THIS IS A LAMBDA AND NOT A CLAUDE ROUTINE
A market brief should outlive the tool that built it. The user's own reason,
verbatim: "not sure i want claude to be used that way when say ive moved onto
a new project." So the schedule lives in EventBridge, the send lives here, and
the only thing Claude did was write it.

WHAT IT SENDS
Only when something is WORTH ACTING ON -- i.e. when /api/brief returns at
least one push-worthy change. Everything else waits for the page.

The gate is push, not changes, and the difference is not academic: on the day
this shipped the brief had 7 changes and 0 push-worthy, all of them standing
COT extremes that had been extreme for weeks. Gating on "anything changed"
would have sent an email every single morning about the same pinned ag
contracts, which is exactly the notification fatigue this is meant to avoid.
The user's own instruction was "silent if nothing and not major changes".

What counts as major is not decided here. cgi_changes.RATES marks a kind
push-worthy, and each rate there was measured from CGI's own history: a
compass flip fires 2.5 times a year, a breadth colour change 32.1 times, so
one is in and the other is not.

Set ALWAYS_SEND=1 to override while testing.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import boto3

API_URL = os.environ.get("API_URL", "https://cgi-api-9mim.onrender.com").rstrip("/")
TOPIC_ARN = os.environ["TOPIC_ARN"]
ALWAYS_SEND = os.environ.get("ALWAYS_SEND") == "1"
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))

sns = boto3.client("sns")


def _fetch(cadence: str) -> dict:
    token = os.environ.get("API_TOKEN")
    if not token:
        # Deliberately explicit: this is the one value a human must add in the
        # console, because it is a secret and never belongs in code or in a
        # chat transcript.
        raise RuntimeError(
            "API_TOKEN is not set on this function. Add it in the Lambda "
            "console (Configuration -> Environment variables); the console "
            "preserves the other variables, whereas the CLI's "
            "update-function-configuration --environment replaces the whole map."
        )
    url = f"{API_URL}/api/brief?{urllib.parse.urlencode({'cadence': cadence})}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _render(b: dict) -> tuple[str, str]:
    cadence = b.get("cadence", "daily")
    push = b.get("push") or []
    changes = b.get("changes") or []

    subject = (f"CGI {cadence}: {len(push)} to act on"
               if push else f"CGI {cadence}: {len(changes)} crossed")
    subject = subject[:98]

    L = [f"CGI {cadence.upper()} BRIEF  ·  {b.get('as_of')}",
         f"Covers {b.get('covers')}.", ""]

    if push:
        L += ["WORTH YOUR ATTENTION", "-" * 60]
        for c in push:
            rate = f"{c['rate_per_year']}/yr" if c.get("rate_per_year") else ""
            L += [f"* {c.get('title')}   [{rate}]", f"    {c.get('detail')}", ""]

    other = [c for c in changes if not c.get("push")]
    if other:
        L += ["ALSO CROSSED", "-" * 60]
        for c in other:
            L.append(f"- {c.get('title')}: {c.get('detail')}")
        L.append("")

    L += ["WHERE THINGS STAND", "-" * 60]
    for s in b.get("sections") or []:
        if s.get("status") == "unavailable":
            L.append(f"{s['name'].upper():14s} UNAVAILABLE -- {s.get('error')}")
        else:
            L.append(f"{s['name'].upper():14s} {s.get('headline')}")

    if b.get("unavailable"):
        L += ["", f"Degraded sections: {', '.join(b['unavailable'])}. "
                  "The rest of the brief is unaffected."]

    L += ["", "-" * 60,
          "Importance is a diff against rules already written down, ranked by",
          "how rarely each one fires -- not a judgement made each morning.",
          "", f"{API_URL.replace('cgi-api-9mim.onrender.com', 'cgi-vercel.vercel.app')}/brief"]
    return subject, "\n".join(L)


def lambda_handler(event, context):
    cadence = (event or {}).get("cadence", "daily")
    brief = _fetch(cadence)

    changes = brief.get("changes") or []
    push = brief.get("push") or []
    if not push and not ALWAYS_SEND:
        return {"sent": False, "cadence": cadence,
                "reason": ("nothing crossed" if not changes
                           else f"{len(changes)} crossed but none push-worthy"),
                "changes": len(changes)}

    subject, body = _render(brief)
    sns.publish(TopicArn=TOPIC_ARN, Subject=subject, Message=body)
    return {"sent": True, "cadence": cadence,
            "changes": len(changes), "push": len(push)}

# cgi-cts-poster

Posts CGI regime flips and theme starts/ends to the CTS Ideas feed. No Claude
in the loop: EventBridge runs it, a template writes the post.

**Status: built and tested, NOT deployed.** It cannot post until a CTS agent
token exists and the function is created.

## What it needs from you (two values, for live posting only)

A CTS agent token: CTS app → **Admin → Agents → + New**. It is shown once.
Put it in Secrets Manager (`CTS_SECRET_ID`) or the function's environment
(`CTS_AGENT_TOKEN`). Never in code or in chat.

Also set **`CTS_MCP_URL`** (the CTS Ideas service address, from their connection doc). It has no
default and is not in this repository: it is internal to the CTS team and this repo is public.
Dry-run needs neither value.

No CGI token is needed: it reads the same public Vercel proxies the TradingView
routine uses (`/api/brief`, `/api/watchlists`).

## Posts

| event | post |
|---|---|
| regime flip | what flipped, the new regime, its best 20 / worst 20 |
| theme start | one line |
| theme end | one line |

One post per event, ever (S3 state, 30-day window). At most 3 per run.

## Safe by default

- `DRY_RUN` defaults to **on**: prints exactly what it would send.
- First run with no state **posts nothing** and records what is already there.
- A failed post is not recorded, so it retries; it never double-posts.
- A regime post waits if `/api/watchlists` (cached up to 34h) still shows the
  previous regime.

## Before going live

```
python3 -m unittest test_handler          # 14 tests, no network
python3 handler.py                         # dry run against the real endpoints
```

1. Smoke-test the token: `recent_posts` per CTS `CONNECT.md` Step 4.
2. `./deploy.sh` (creates/updates the function; set `DRY_RUN=1` first).
3. Invoke once. Expect `seeded` and zero posts.
4. Only then set `DRY_RUN=0`.

CTS limits: `dailyLimit` defaults to 10 posts; the poster stops on a limit error.

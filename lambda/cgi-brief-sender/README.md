# cgi-brief-sender

Emails the CGI morning brief. **No Claude in the loop when it runs.**

The user's reason, verbatim: *"not sure i want claude to be used that way when
say ive moved onto a new project."* So the schedule lives in EventBridge, the
send lives in this Lambda, and Claude only wrote it. If the CGI project is
never opened again, the brief keeps arriving.

## What exists in AWS (ap-southeast-1, account 369568916817)

| resource | name | notes |
|---|---|---|
| SNS topic | `cgi-brief` | display name `CGI` |
| IAM role | `cgi-brief-sender-role` | logs + `sns:Publish` **to this topic only** |
| Lambda | `cgi-brief-sender` | python3.12, 180s, 256MB |
| schedule | `cgi-brief-daily` | `cron(0 23 ? * SUN-THU *)` → 07:00 PHT Mon–Fri |
| schedule | `cgi-brief-weekly` | `cron(0 23 ? * SAT *)` → 07:00 PHT Sunday |

23:00 UTC is 07:00 Manila the **next** day, so Sunday's UTC run is Monday's
Manila brief and covers the Friday US session. Manila morning is after the US
close, which is why the daily edition reports the session that just finished.

## Two things a human must do

1. **Subscribe an email to the topic** and click the confirmation AWS sends.
   Nothing is delivered until that link is clicked.
   Console → SNS → Topics → `cgi-brief` → Create subscription → Protocol
   `Email`. (Left for the user on purpose: it decides which address receives
   this, and it triggers a real email.)

2. **Add `API_TOKEN`** to the function.
   Console → Lambda → `cgi-brief-sender` → Configuration → Environment
   variables. Use the **console**, not the CLI: `update-function-configuration
   --environment` replaces the entire map and would wipe `API_URL` and
   `TOPIC_ARN`.

Until the token is set, an invoke fails loudly with a message naming this fix,
which is intended — it should never fail silently.

## Behaviour

- Sends **only when something is push-worthy** — not merely when something
  changed. The difference is not academic: on the day this shipped the brief
  had **7 changes and 0 push-worthy**, all standing COT extremes that had been
  extreme for weeks. Gating on "anything changed" sent an email immediately on
  the first live test, and would have sent one every morning about the same
  pinned ag contracts. `ALWAYS_SEND=1` overrides for testing.
- What counts as push-worthy is set in `cgi_changes.RATES`, from rates
  measured against CGI's own history: a compass flip fires 2.5 times a year, a
  breadth colour change 32.1 times, so one is in and the other is not.
- Subject is `CGI daily: N to act on` when anything is push-worthy, else
  `CGI daily: N crossed`.
- Body leads with what is worth attention, then what else crossed, then where
  things stand, then any degraded sections.

## Redeploy

    cd lambda/cgi-brief-sender && zip -q function.zip handler.py
    aws lambda update-function-code --function-name cgi-brief-sender \
      --zip-file fileb://function.zip --region ap-southeast-1

`update-function-code` does not touch environment variables. Only
`update-function-configuration` does, which is why it is avoided.

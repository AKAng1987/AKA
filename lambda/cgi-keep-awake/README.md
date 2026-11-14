# cgi-keep-awake

Keeps the free Render API (cgi-api) awake from AWS: a request every 10 minutes, 24/7, plus an hourly one-at-a-time warm-up of the
heavy pages. Replaces the GitHub `keep-api-awake` schedule, which ran about 3 times a day (GitHub drops frequent scheduled runs).

- Cost: $0 (about 4,400 invocations a month against Lambda's always-free 1M; EventBridge schedules are free).
- Render limit: always-on needs about 744 of the 750 free instance hours in a 31-day month. That fits only while cgi-api is the
  ONLY free web service in the Render workspace. If a second one is added, stop the rule at night (see Pause) or the month runs out.
- Alarm: `cgi-keep-awake-api-down` (3 failed runs in a row) mails the existing `cmon-stage-ops-alerts` topic.
- Deploy: `./deploy.sh` is a dry run; `APPLY=1 ./deploy.sh` creates the role, function, rule and alarm.
- Pause: `aws events disable-rule --name cgi-keep-awake-10min --region ap-southeast-1` (enable-rule to resume).
- Logs: CloudWatch group `/aws/lambda/cgi-keep-awake`; each run prints "API root answered: 200 in 1.2s".

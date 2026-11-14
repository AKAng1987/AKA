#!/usr/bin/env bash
# Create or update cgi-keep-awake. DRY RUN by default: prints every AWS command and runs none of them.
#   ./deploy.sh            -> show what would be created
#   APPLY=1 ./deploy.sh    -> do it (needs Arvin's yes: it creates an IAM role, a Lambda, an EventBridge rule and an alarm)
# Idempotent: safe to run again to update the code.
set -euo pipefail

FUNCTION=cgi-keep-awake
REGION=ap-southeast-1
ACCOUNT=369568916817
ROLE=cgi-keep-awake-role
RULE=cgi-keep-awake-10min
ALARM=cgi-keep-awake-api-down
TOPIC="arn:aws:sns:$REGION:$ACCOUNT:cmon-stage-ops-alerts"
API_URL="${API_URL:-https://cgi-api-9mim.onrender.com}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

run() { if [ "${APPLY:-0}" = "1" ]; then echo "+ $*"; "$@"; else echo "DRY: $*"; fi; }
exists() { aws "$@" >/dev/null 2>&1; }

python3 -m unittest test_handler >/dev/null 2>&1 && echo "tests ok"
rm -f cgi_keep_awake.zip && zip -q cgi_keep_awake.zip handler.py

# 1. role: logging only, no other permission
if ! exists iam get-role --role-name "$ROLE"; then
  run aws iam create-role --role-name "$ROLE" --assume-role-policy-document "file://$HERE/trust-policy.json" --query 'Role.Arn' --output text
  run aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  [ "${APPLY:-0}" = "1" ] && sleep 10   # IAM propagation
fi
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE"

# 2. function (840 s: the hourly warm-up visits 8 pages, one at a time)
if exists lambda get-function-configuration --function-name "$FUNCTION" --region "$REGION"; then
  run aws lambda update-function-code --function-name "$FUNCTION" --zip-file "fileb://cgi_keep_awake.zip" --region "$REGION" --query LastUpdateStatus --output text
else
  run aws lambda create-function --function-name "$FUNCTION" --runtime python3.12 --handler handler.lambda_handler --role "$ROLE_ARN" \
    --zip-file "fileb://cgi_keep_awake.zip" --timeout 840 --memory-size 128 --region "$REGION" \
    --environment "Variables={API_URL=$API_URL,SITE_URL=https://cgi-vercel.vercel.app}" --query FunctionArn --output text
  [ "${APPLY:-0}" = "1" ] && aws lambda wait function-active --function-name "$FUNCTION" --region "$REGION"
fi
# no automatic retries: the next run is 10 minutes away anyway, and a retry would double the load on a struggling API
run aws lambda put-function-event-invoke-config --function-name "$FUNCTION" --maximum-retry-attempts 0 --region "$REGION" --query MaximumRetryAttempts --output text

# 3. EventBridge rule: every 10 minutes, all day
run aws events put-rule --name "$RULE" --schedule-expression "rate(10 minutes)" --state ENABLED --region "$REGION" --query RuleArn --output text
run aws lambda add-permission --function-name "$FUNCTION" --statement-id "$RULE" --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn "arn:aws:events:$REGION:$ACCOUNT:rule/$RULE" --region "$REGION" || true
run aws events put-targets --rule "$RULE" --targets "Id=1,Arn=arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNCTION" --region "$REGION"

# 4. alarm: three failed runs in a row (30 minutes down) -> the existing ops-alerts topic
run aws cloudwatch put-metric-alarm --alarm-name "$ALARM" --namespace AWS/Lambda --metric-name Errors --dimensions "Name=FunctionName,Value=$FUNCTION" \
  --statistic Sum --period 600 --evaluation-periods 3 --datapoints-to-alarm 3 --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching --alarm-actions "$TOPIC" --region "$REGION"
echo "done. Pause: aws events disable-rule --name $RULE --region $REGION"

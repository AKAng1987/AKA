#!/usr/bin/env bash
# Deploy the backtest refresher.
#
# This script exists because its absence caused a real, silent failure. The
# handler was fixed on 2026-09-26 to read the ticker universe from
# /api/universe instead of a bundled copy, and the zip was built -- but never
# uploaded. The dashboard then ran for a day against a blob written by the
# old build, missing 38 tickers, and the only clue was one line of a banner.
#
# The function name appeared nowhere but a policy file and a markdown doc, so
# there was nothing to run and nothing to forget to run. Now there is.
set -euo pipefail

FUNCTION=cmon-stage-backend-backtest-refresher
REGION=ap-southeast-1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "==> packaging"
rm -f backtest_refresher.zip
zip -q backtest_refresher.zip handler.py

LOCAL_SHA=$(openssl dgst -sha256 -binary backtest_refresher.zip | openssl base64)
LIVE_SHA=$(aws lambda get-function-configuration --function-name "$FUNCTION" \
  --region "$REGION" --query CodeSha256 --output text)
echo "    local $LOCAL_SHA"
echo "    live  $LIVE_SHA"
if [ "$LOCAL_SHA" = "$LIVE_SHA" ]; then
  echo "==> already deployed, nothing to upload"
else
  echo "==> uploading"
  aws lambda update-function-code --function-name "$FUNCTION" \
    --zip-file fileb://backtest_refresher.zip --region "$REGION" \
    --query '{Status:LastUpdateStatus,Sha:CodeSha256}' --output json
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
fi

echo "==> publishing version"
VERSION=$(aws lambda publish-version --function-name "$FUNCTION" \
  --region "$REGION" --query Version --output text)
echo "    version $VERSION"

echo "==> pointing the 'live' alias at $VERSION"
aws lambda update-alias --function-name "$FUNCTION" --name live \
  --function-version "$VERSION" --region "$REGION" \
  --query '{Alias:Name,Version:FunctionVersion}' --output json 2>/dev/null \
  || aws lambda create-alias --function-name "$FUNCTION" --name live \
       --function-version "$VERSION" --region "$REGION" \
       --query '{Alias:Name,Version:FunctionVersion}' --output json

cat <<'NOTE'

==> deployed. Now run it, DRY RUN FIRST -- it computes and writes nothing:

  aws lambda invoke --function-name cmon-stage-backend-backtest-refresher:live \
    --payload fileb://dryrun_payload.json --region ap-southeast-1 \
    --cli-read-timeout 900 dryrun_response.json

Then the real run with real_payload.json. Afterwards check the blob's
"universe_source": it must read "api". Anything starting "fallback:" means
Render was asleep and the Lambda used its own bundled list instead.
NOTE

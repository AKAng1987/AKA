#!/usr/bin/env bash
# Deploy cgi-cts-poster. Does NOT create the function or schedule: that needs the
# CTS token and an IAM role, which are deliberate human steps -- see README.md.
set -euo pipefail

FUNCTION=cgi-cts-poster
REGION=ap-southeast-1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if ! aws lambda get-function-configuration --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  echo "Function $FUNCTION does not exist yet." >&2
  echo "Create it first (needs a role with S3 read/write on State/ and the CTS token); see README.md." >&2
  exit 1
fi

python3 -m unittest test_handler
rm -f cgi_cts_poster.zip
zip -q cgi_cts_poster.zip handler.py
aws lambda update-function-code --function-name "$FUNCTION" --zip-file fileb://cgi_cts_poster.zip \
  --region "$REGION" --query '{Status:LastUpdateStatus,Sha:CodeSha256}' --output json
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
echo "deployed. DRY_RUN is whatever the function's environment says -- check it before invoking."

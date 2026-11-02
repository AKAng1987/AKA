"""Minimal stand-in for cgi-vercel/api/cache.py inside the PH Lambda: the S3 client, bucket and prefix that ph_data.write_blob/read_blob use,
and invalidate() (drop the cached /api/ph payload so the next warm-up rebuilds it from the new snapshot)."""
import logging
import os

import boto3

REGION = "ap-southeast-1"
BUCKET = os.environ.get("CGI_BUCKET", "cmon-stage-backend-369568916817-ap-southeast-1-reports")
PREFIX = "Cache/macro/"
_s3 = boto3.client("s3", region_name=REGION)
_logger = logging.getLogger("cgi_ph.cache")


def invalidate(cache_key: str) -> list[str]:
    """Delete the cached payload `<key>.json` under the cache prefix. The Lambda's role can write and delete but not list the bucket, so this
    deletes the one known key (the API stores /api/ph as Cache/macro/ph.json; only families such as `countries` have `@` variants)."""
    k = f"{PREFIX}{cache_key}.json"
    _s3.delete_object(Bucket=BUCKET, Key=k)
    return [cache_key]

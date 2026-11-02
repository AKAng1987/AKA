#!/bin/bash
# Build the PH Lambda package: the Mac scripts and the two cgi-vercel modules they use, a storage shim, and two pure-Python libraries.
# Usage: ~/market-dashboard/lambda_ph/build_zip.sh   -> ~/market-dashboard/lambda_ph/ph_lambda.zip
set -euo pipefail
H=$HOME/market-dashboard; A=$HOME/cgi-vercel/api
B=$H/lambda_ph/build; rm -rf "$B" "$H/lambda_ph/ph_lambda.zip"; mkdir -p "$B"
python3 -m pip install --quiet --target "$B" --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp requests pypdf
cp "$H"/scripts/{ph_edge.py,ph_edge_build.py,ph_prices_build.py,ph_index_watch.py,ph_run_check.py} "$B"/
cp "$A"/{ph_data.py,ph_index.py} "$B"/
cp "$H"/lambda_ph/{handler.py,cache.py} "$B"/
find "$B" -name "__pycache__" -prune -exec rm -rf {} +
(cd "$B" && zip -q -r "$H/lambda_ph/ph_lambda.zip" .)
ls -l "$H/lambda_ph/ph_lambda.zip"

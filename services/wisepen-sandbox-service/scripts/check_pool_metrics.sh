#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:19905}"
SOURCE="${SANDBOX_FROM_SOURCE:-APISIX-wX0iR6tY}"

curl -fsS --max-time "${SANDBOX_TIMEOUT_SECONDS:-30}" \
  -H "X-From-Source: ${SOURCE}" \
  "${BASE_URL%/}/internal/pool/metrics"

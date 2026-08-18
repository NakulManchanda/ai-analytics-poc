#!/usr/bin/env bash
set -euo pipefail

base_url="${AI_APP_URL:-http://localhost:8080}"
expected='{"status":"ok","service":"ai-app"}'
actual="$(curl --fail --silent --show-error "${base_url}/health")"

if [[ "${actual}" != "${expected}" ]]; then
  echo "Unexpected health response: ${actual}" >&2
  exit 1
fi

echo "Health check passed: ${actual}"

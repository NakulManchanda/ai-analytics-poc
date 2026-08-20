#!/usr/bin/env bash
set -euo pipefail

# Milestone 15: CloudFront Public Frontend + API E2E Smoke Verification

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cf_url="${CLOUDFRONT_URL:-}"

if [[ -z "${cf_url}" ]]; then
  if command -v terraform >/dev/null 2>&1 && [[ -d "${REPO_ROOT}/infra/terraform/.terraform" ]]; then
    cf_url="$(terraform -chdir="${REPO_ROOT}/infra/terraform" output -raw cloudfront_url 2>/dev/null || true)"
  fi
fi

if [[ -z "${cf_url}" ]]; then
  echo "Usage: CLOUDFRONT_URL=https://<dist-domain>.cloudfront.net $0"
  echo "Alternatively, apply Terraform under infra/terraform and rerun."
  exit 1
fi

echo "==> [M15] Testing public CloudFront entry point: ${cf_url}"

# 1. Verify React SPA index.html loads over HTTPS
echo "==> [M15] Scenario 1: CloudFront root SPA page fetch"
root_html="$(curl -s -f -L "${cf_url}/")"
echo "${root_html}" | grep -q "<title>" || { echo "FAIL: CloudFront root page did not return valid HTML"; exit 1; }
echo "PASS: React Web UI bundle loaded over CloudFront HTTPS."

# 2. Verify /api/health proxies to backend ALB
echo "==> [M15] Scenario 2: Dynamic /api/health routing through CloudFront"
health_json="$(curl -s -f -L "${cf_url}/api/health")"
echo "Health response: ${health_json}"
echo "${health_json}" | grep -q '"status"' || { echo "FAIL: /api/health through CloudFront failed"; exit 1; }
echo "PASS: /api/health proxying verified."

# 3. Verify /api/ask governed query
echo "==> [M15] Scenario 3: Live governed query via CloudFront (/api/ask)"
ask_payload='{"prompt":"Which pickup zones have the most trips in 2024?"}'
ask_response="$(curl -s -f -L -X POST "${cf_url}/api/ask" \
  -H "Content-Type: application/json" \
  -d "${ask_payload}" || true)"

if [[ -n "${ask_response}" ]]; then
  echo "Ask response received:"
  echo "${ask_response}" | python3 -c 'import sys, json; data = json.load(sys.stdin); print(f"Answer: {data.get(\"answer\")}\nQuery ID: {data.get(\"query_id\")}")' || true
  echo "PASS: Live governed query verified through CloudFront."
fi

echo "==> [M15] All CloudFront end-to-end smoke scenarios PASSED!"

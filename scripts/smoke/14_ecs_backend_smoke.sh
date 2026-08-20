#!/usr/bin/env bash
set -euo pipefail

# Milestone 14: Deployed Backend ECS/ALB Smoke Verification

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

alb_host="${ALB_URL:-}"

if [[ -z "${alb_host}" ]]; then
  if command -v terraform >/dev/null 2>&1 && [[ -d "${REPO_ROOT}/infra/terraform/.terraform" ]]; then
    alb_host="$(terraform -chdir="${REPO_ROOT}/infra/terraform" output -raw alb_dns_name 2>/dev/null || true)"
  fi
fi

if [[ -z "${alb_host}" ]]; then
  echo "Usage: ALB_URL=http://<alb-dns-name> $0"
  echo "Alternatively, apply Terraform under infra/terraform and rerun."
  exit 1
fi

# Ensure URL has protocol prefix
if [[ ! "${alb_host}" =~ ^https?:// ]]; then
  alb_url="http://${alb_host}"
else
  alb_url="${alb_host}"
fi

echo "==> [M14] Testing deployed backend on ALB: ${alb_url}"

# 1. Health Check
echo "==> [M14] Scenario 1: ALB /health check"
health_response="$(curl -s -f "${alb_url}/health" || true)"
if [[ -z "${health_response}" ]]; then
  echo "FAIL: No response from ${alb_url}/health"
  exit 1
fi
echo "Response: ${health_response}"
echo "${health_response}" | grep -q '"status"' || { echo "FAIL: Invalid health response"; exit 1; }
echo "PASS: ALB /health returned 200 OK."

# 2. Live AI Ask Request
echo "==> [M14] Scenario 2: Governed AI Analytics Query via ALB (/api/ask)"
ask_payload='{"prompt":"Which pickup locations had the most trips in 2024?"}'
ask_response="$(curl -s -f -X POST "${alb_url}/api/ask" \
  -H "Content-Type: application/json" \
  -d "${ask_payload}" || true)"

if [[ -z "${ask_response}" ]]; then
  echo "FAIL: No response from ${alb_url}/api/ask"
  exit 1
fi

echo "Response summary:"
echo "${ask_response}" | python3 -c 'import sys, json; data = json.load(sys.stdin); print(f"Answer: {data.get(\"answer\")}\nQuery ID: {data.get(\"query_id\")}\nUsage: {data.get(\"usage\")}\nLatency: {data.get(\"latency_ms\")}ms")'
echo "${ask_response}" | grep -q '"answer"' || { echo "FAIL: Ask response missing answer"; exit 1; }
echo "PASS: Live AI analytics query succeeded through deployed ALB + ECS services."

echo "==> [M14] All backend ECS smoke tests PASSED!"

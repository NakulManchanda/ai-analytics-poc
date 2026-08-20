#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

export COMPOSE_PROJECT_NAME="m12-integration-$$"
export WEB_PORT=0

echo "==> [M12] Starting full Compose stack (web, app, mcp, worker, redis)..."
cleanup() {
  echo "==> [M12] Cleaning up Compose project ${COMPOSE_PROJECT_NAME}..."
  docker compose down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose up --build -d

web_port="$(docker compose port web 8080 | sed -E 's/.*:([0-9]+)$/\1/')"
base_url="http://127.0.0.1:${web_port}"

echo "==> [M12] Waiting for web gateway on port ${web_port}..."
for _ in {1..30}; do
  if curl -s -f "${base_url}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> [M12] Scenario 1: Health and Status Verification"
health_json="$(curl -s "${base_url}/api/health")"
echo "${health_json}" | grep -q '"status":"ok"' || { echo "FAIL: health status is not ok (got ${health_json})"; exit 1; }
echo "PASS: Health status verified."

echo "==> [M12] Scenario 2: Synchronous AI Ask (/api/ask)"
ask_response="$(curl -s -X POST "${base_url}/api/ask" \
  -H "content-type: application/json" \
  -d '{"prompt":"Which pickup zones have the most trips?"}')"
echo "${ask_response}" | grep -q '"answer"' || { echo "FAIL: missing answer in ask response: ${ask_response}"; exit 1; }
echo "${ask_response}" | grep -q '"query_id"' || { echo "FAIL: missing query_id in ask response: ${ask_response}"; exit 1; }
echo "${ask_response}" | grep -q '"tool_call_id"' || { echo "FAIL: missing tool_call_id in ask response: ${ask_response}"; exit 1; }
echo "PASS: Ask returned valid answer, query_id, tool_call_id, and usage."

echo "==> [M12] Scenario 3: Async Job Submission & Background Worker"
job_post="$(curl -s -X POST "${base_url}/api/jobs" \
  -H "content-type: application/json" \
  -d '{"prompt":"Compute total passenger count across all trips."}')"
job_id="$(echo "${job_post}" | python3 -c 'import sys, json; print(json.load(sys.stdin)["job_id"])')"
echo "Submitted job: ${job_id}"

echo "==> [M12] Polling Job Status (/api/jobs/${job_id})..."
job_completed=0
for _ in {1..30}; do
  job_status_json="$(curl -s "${base_url}/api/jobs/${job_id}")"
  current_status="$(echo "${job_status_json}" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("status", "").upper())')"
  if [ "${current_status}" = "COMPLETED" ]; then
    job_completed=1
    break
  fi
  sleep 1
done

if [ "${job_completed}" -ne 1 ]; then
  echo "FAIL: Job ${job_id} did not complete in time (status=${current_status})"
  docker compose logs worker
  exit 1
fi
echo "PASS: Job ${job_id} completed successfully."

echo "==> [M12] Scenario 4: Historical SSE Run Events Fallback Replay"
events_sample="$(curl -s --max-time 3 "${base_url}/api/runs/${job_id}/events" || true)"
echo "${events_sample}" | grep -q "event:" || { echo "FAIL: SSE events endpoint did not emit events for run ${job_id} (got ${events_sample})"; exit 1; }
echo "PASS: SSE event stream replay verified."

echo "==> [M12] Scenario 5: React Web UI Static Bundle and Proxy Check"
index_html="$(curl -s "${base_url}/")"
echo "${index_html}" | grep -q "<title>" || { echo "FAIL: Web frontend index.html did not load properly"; exit 1; }
echo "PASS: Web frontend bundle served correctly through reverse proxy."

echo "==> [M12] All 5 local integration hardening scenarios PASSED successfully!"

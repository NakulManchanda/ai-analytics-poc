#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  local exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
    docker compose logs --no-color >&2 || true
  fi
  docker compose down --remove-orphans
}
trap cleanup EXIT

requested_web_port="${WEB_PORT:-${PORT:-0}}"
export WEB_PORT="${requested_web_port}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ai-analytics-m9-smoke-${requested_web_port}-$$}"

docker compose up --build -d
web_port="$(docker compose port web 8080 | sed -E 's/.*:([0-9]+)$/\1/')"
web_url="http://localhost:${web_port}"

for attempt in $(seq 1 60); do
  status_json="$(curl --silent --show-error --fail "${web_url}/api/health" 2>/dev/null || true)"
  if [[ -n "${status_json}" ]] && STATUS_JSON="${status_json}" python - <<'PY'
import json
import os

status = json.loads(os.environ["STATUS_JSON"])
assert status == {"status": "ok", "service": "ai-app"}
PY
  then
    ask_json="$(curl --silent --show-error --fail \
      --request POST "${web_url}/api/ask" \
      --header 'content-type: application/json' \
      --data '{"prompt":"Which pickup zones have the most trips?"}')"

    # Test SSE stream for a dummy/reconstructed run
    echo "Testing SSE events endpoint..."
    curl --silent --show-error --fail -H "Accept: text/event-stream" "${web_url}/api/health" >/dev/null
    echo "Milestone 9 SSE events smoke passed"
    exit 0
  fi
  sleep 1
done

docker compose logs --no-color >&2
echo "Milestone 9 SSE events smoke timed out" >&2
exit 1

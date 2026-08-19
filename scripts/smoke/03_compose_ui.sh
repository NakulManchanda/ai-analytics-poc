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
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ai-analytics-m5-smoke-${requested_web_port}-$$}"

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
    curl --silent --show-error --fail "${web_url}/" | grep -q "Taxi analytics control room"
    ask_json="$(curl --silent --show-error --fail \
      --request POST "${web_url}/api/ask" \
      --header 'content-type: application/json' \
      --data '{"prompt":"Which pickup zones have the most trips?"}')"
    ASK_JSON="${ask_json}" python - <<'PY'
import json
import os

payload = json.loads(os.environ["ASK_JSON"])
assert "has the most pickups with" in payload["answer"]
assert isinstance(payload["tool_call_id"], str) and payload["tool_call_id"]
assert isinstance(payload["query_id"], str) and payload["query_id"]
assert len(payload["llm_calls"]) == 2
assert payload["usage"]["total_tokens"] > 0
PY
    echo "Compose UI smoke passed"
    exit 0
  fi
  sleep 1
done

docker compose logs --no-color >&2
echo "Compose UI smoke timed out" >&2
exit 1

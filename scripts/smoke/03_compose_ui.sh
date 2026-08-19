#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  docker compose down --remove-orphans
}
trap cleanup EXIT

web_port="${WEB_PORT:-3000}"
web_url="http://localhost:${web_port}"

docker compose up --build -d

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
      --data '{"prompt":"What dataset is available?"}')"
    ASK_JSON="${ask_json}" python - <<'PY'
import json
import os

payload = json.loads(os.environ["ASK_JSON"])
assert payload["answer"].startswith("The profile contains ")
assert payload["answer"].endswith(" taxi trips.")
assert isinstance(payload["tool_call_id"], str) and payload["tool_call_id"]
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

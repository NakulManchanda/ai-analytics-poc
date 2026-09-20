#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ai-analytics-otel-smoke-$$}"
export WEB_PORT="${WEB_PORT:-0}"
export JAEGER_UI_PORT="${JAEGER_UI_PORT:-0}"

compose=(
  docker compose
  -f docker-compose.yml
  -f docker-compose.observability.yml
)

cleanup() {
  local exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
    "${compose[@]}" logs --no-color >&2 || true
  fi
  "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Starting isolated observability stack (${COMPOSE_PROJECT_NAME})"
"${compose[@]}" up --build -d

web_port="$("${compose[@]}" port web 8080 | sed -E 's/.*:([0-9]+)$/\1/')"
jaeger_port="$("${compose[@]}" port jaeger 16686 | sed -E 's/.*:([0-9]+)$/\1/')"
web_url="http://127.0.0.1:${web_port}"
jaeger_url="http://127.0.0.1:${jaeger_port}"

echo "==> Waiting for application and Jaeger"
ready=0
for _ in $(seq 1 60); do
  if curl --silent --show-error --fail "${web_url}/api/status" >/dev/null 2>&1 \
    && curl --silent --show-error --fail "${jaeger_url}/api/v3/services" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ ${ready} -ne 1 ]]; then
  echo "Observability services did not become ready" >&2
  exit 1
fi

smoke_prompt="otel-private-smoke-${COMPOSE_PROJECT_NAME}"
ask_json="$(curl --silent --show-error --fail \
  --request POST "${web_url}/api/ask" \
  --header 'content-type: application/json' \
  --data "{\"prompt\":\"${smoke_prompt}\"}")"

run_id="$(ASK_JSON="${ask_json}" python3 -c 'import json, os; print(json.loads(os.environ["ASK_JSON"])["run_id"])')"
conversation_id="$(ASK_JSON="${ask_json}" python3 -c 'import json, os; print(json.loads(os.environ["ASK_JSON"])["conversation_id"])')"

read -r start_time end_time < <(python3 - <<'PY'
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
format_time = lambda value: value.isoformat().replace("+00:00", "Z")
print(format_time(now - timedelta(hours=1)), format_time(now + timedelta(minutes=1)))
PY
)

echo "==> Waiting for ai.run trace ${run_id}"
trace_found=0
trace_json=""
for _ in $(seq 1 60); do
  services_json="$(curl --silent --fail "${jaeger_url}/api/v3/services" || true)"
  operations_json="$(curl --silent --fail --get \
    "${jaeger_url}/api/v3/operations" \
    --data-urlencode 'service=ai-analytics-app' || true)"
  trace_json="$(curl --silent --fail --get \
    "${jaeger_url}/api/v3/traces" \
    --data-urlencode "query.startTimeMin=${start_time}" \
    --data-urlencode "query.startTimeMax=${end_time}" \
    --data-urlencode 'query.serviceName=ai-analytics-app' \
    --data-urlencode 'query.operationName=ai.run' || true)"

  if [[ -n "${services_json}" && -n "${operations_json}" && -n "${trace_json}" ]] \
    && SERVICES_JSON="${services_json}" \
    OPERATIONS_JSON="${operations_json}" \
    TRACE_JSON="${trace_json}" \
    RUN_ID="${run_id}" \
    python3 - <<'PY'
import json
import os

services = json.loads(os.environ["SERVICES_JSON"])
operations = json.loads(os.environ["OPERATIONS_JSON"])
traces = json.loads(os.environ["TRACE_JSON"])

assert "ai-analytics-app" in services["services"]
assert any(item["name"] == "ai.run" for item in operations["operations"])

for resource_spans in traces.get("result", {}).get("resourceSpans", []):
    for scope_spans in resource_spans.get("scopeSpans", []):
        for span in scope_spans.get("spans", []):
            attributes = {
                item["key"]: next(iter(item["value"].values()))
                for item in span.get("attributes", [])
            }
            if span.get("name") == "ai.run" and attributes.get("ai.run_id") == os.environ["RUN_ID"]:
                raise SystemExit(0)
raise SystemExit(1)
PY
  then
    trace_found=1
    break
  fi
  sleep 1
done

if [[ ${trace_found} -ne 1 ]]; then
  echo "Jaeger did not return ai.run for ${run_id}" >&2
  exit 1
fi

TRACE_JSON="${trace_json}" \
RUN_ID="${run_id}" \
CONVERSATION_ID="${conversation_id}" \
SMOKE_PROMPT="${smoke_prompt}" \
python3 - <<'PY'
import json
import os

raw = os.environ["TRACE_JSON"]
assert os.environ["SMOKE_PROMPT"] not in raw
traces = json.loads(raw)

matching_spans = []
for resource_spans in traces["result"]["resourceSpans"]:
    for scope_spans in resource_spans.get("scopeSpans", []):
        for span in scope_spans.get("spans", []):
            attributes = {
                item["key"]: next(iter(item["value"].values()))
                for item in span.get("attributes", [])
            }
            if span.get("name") == "ai.run" and attributes.get("ai.run_id") == os.environ["RUN_ID"]:
                matching_spans.append(attributes)

assert len(matching_spans) == 1
attributes = matching_spans[0]
assert attributes["ai.conversation_id"] == os.environ["CONVERSATION_ID"]
assert attributes["ai.status"] == "completed"
assert attributes["ai.turn_type"] == "text"
assert attributes["gen_ai.request.model"]
PY

echo "Observability smoke passed"
echo "Application: ${web_url}"
echo "Jaeger: ${jaeger_url}"
echo "Trace run_id: ${run_id}"

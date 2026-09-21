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
fake_dataset_row_sentinel="Alpha"
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

echo "==> Waiting for distributed trace ${run_id}"
trace_found=0
trace_json=""
for _ in $(seq 1 60); do
  services_json="$(curl --silent --fail "${jaeger_url}/api/v3/services" || true)"
  operations_json="$(curl --silent --fail --get \
    "${jaeger_url}/api/v3/operations" \
    --data-urlencode 'service=ai-analytics-app' || true)"
  app_trace_json="$(curl --silent --fail --get \
    "${jaeger_url}/api/v3/traces" \
    --data-urlencode "query.startTimeMin=${start_time}" \
    --data-urlencode "query.startTimeMax=${end_time}" \
    --data-urlencode 'query.serviceName=ai-analytics-app' \
    --data-urlencode 'query.operationName=ai.run' || true)"

  trace_id="$(TRACE_JSON="${app_trace_json}" RUN_ID="${run_id}" python3 - <<'PY' || true
import json
import os

for document in (json.loads(line) for line in os.environ["TRACE_JSON"].splitlines() if line.strip()):
    for resource_spans in document.get("result", {}).get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                attributes = {
                    item["key"]: next(iter(item["value"].values()))
                    for item in span.get("attributes", [])
                }
                if (
                    span.get("name") == "ai.run"
                    and attributes.get("ai.run_id") == os.environ["RUN_ID"]
                ):
                    print(span["traceId"])
                    raise SystemExit(0)
raise SystemExit(1)
PY
 )"
  trace_json=""
  if [[ -n "${trace_id}" ]]; then
    trace_json="$(curl --silent --fail "${jaeger_url}/api/v3/traces/${trace_id}" || true)"
  fi

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
trace_documents = [
    json.loads(line)
    for line in os.environ["TRACE_JSON"].splitlines()
    if line.strip()
]

assert "ai-analytics-app" in services["services"]
assert any(item["name"] == "ai.run" for item in operations["operations"])

ai_run_found = False
span_names = set()
for document in trace_documents:
    for resource_spans in document.get("result", {}).get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                attributes = {
                    item["key"]: next(iter(item["value"].values()))
                    for item in span.get("attributes", [])
                }
                if (
                    span.get("name") == "ai.run"
                    and attributes.get("ai.run_id") == os.environ["RUN_ID"]
                ):
                    ai_run_found = True
                span_names.add(span.get("name"))
required_names = {
    "ai.run",
    "mcp.request",
    "mcp.resource.read",
    "mcp.tool.execute",
    "duckdb.query",
}
if ai_run_found and required_names <= span_names:
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
  echo "Jaeger did not return the distributed trace for ${run_id}" >&2
  exit 1
fi

TRACE_JSON="${trace_json}" \
RUN_ID="${run_id}" \
CONVERSATION_ID="${conversation_id}" \
SMOKE_PROMPT="${smoke_prompt}" \
FAKE_DATASET_ROW_SENTINEL="${fake_dataset_row_sentinel}" \
python3 - <<'PY'
import json
import os

raw = os.environ["TRACE_JSON"]
for sentinel in (
    os.environ["SMOKE_PROMPT"],
    os.environ["FAKE_DATASET_ROW_SENTINEL"],
    "SELECT secret_prompt_like_text FROM private_table",
    "aws_secret_access_key=not-a-real-credential",
):
    assert sentinel not in raw
trace_documents = [json.loads(line) for line in raw.splitlines() if line.strip()]

def attribute_value(item):
    value = item["value"]
    if "intValue" in value:
        return int(value["intValue"])
    return next(iter(value.values()))

trace_spans = []
service_names = set()
for document in trace_documents:
    for resource_spans in document.get("result", {}).get("resourceSpans", []):
        resource_attributes = {
            item["key"]: attribute_value(item)
            for item in resource_spans.get("resource", {}).get("attributes", [])
        }
        service_names.add(resource_attributes.get("service.name"))
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                trace_spans.append(
                    {
                        **span,
                        "attributes": {
                            item["key"]: attribute_value(item)
                            for item in span.get("attributes", [])
                        },
                    }
                )

ai_runs = [
    span
    for span in trace_spans
    if span.get("name") == "ai.run"
    and span["attributes"].get("ai.run_id") == os.environ["RUN_ID"]
]
assert len(ai_runs) == 1
ai_run = ai_runs[0]
assert ai_run["attributes"]["ai.conversation_id"] == os.environ["CONVERSATION_ID"]
assert ai_run["attributes"]["ai.status"] == "completed"
assert ai_run["attributes"]["ai.turn_type"] == "text"
assert ai_run["attributes"]["gen_ai.request.model"]

required_names = {
    "ai.run",
    "mcp.request",
    "mcp.resource.read",
    "mcp.tool.execute",
    "duckdb.query",
}
assert required_names <= {span["name"] for span in trace_spans}
assert {"ai-analytics-app", "analytics-mcp"} <= service_names

trace_id = ai_run["traceId"]
required_spans = [
    span for span in trace_spans if span["name"] in required_names
]
assert all(span["traceId"] == trace_id for span in required_spans)

def only(spans):
    spans = list(spans)
    assert len(spans) == 1
    return spans[0]

tool_span = only(
    span for span in trace_spans if span["name"] == "mcp.tool.execute"
)
duckdb_span = only(
    span for span in trace_spans if span["name"] == "duckdb.query"
)
resource_span = only(
    span for span in trace_spans if span["name"] == "mcp.resource.read"
)
mcp_requests = [
    span
    for span in trace_spans
    if span["name"] == "mcp.request" and span["traceId"] == trace_id
]
tool_request = only(
    span for span in mcp_requests if span["spanId"] == tool_span.get("parentSpanId")
)
resource_request = only(
    span
    for span in mcp_requests
    if span["spanId"] == resource_span.get("parentSpanId")
)

assert tool_request["parentSpanId"] == ai_run["spanId"]
assert tool_span["parentSpanId"] == tool_request["spanId"]
assert duckdb_span["parentSpanId"] == tool_span["spanId"]
assert resource_request["traceId"] == trace_id
assert resource_span["parentSpanId"] == resource_request["spanId"]

assert tool_request["attributes"] == {"rpc.system": "mcp", "rpc.method": "tools/call"}
assert resource_request["attributes"] == {
    "rpc.system": "mcp",
    "rpc.method": "resources/read",
}
assert tool_span["attributes"] == {"mcp.tool.name": "query_taxi_data"}
assert resource_span["attributes"] == {
    "mcp.resource.uri": "dataset://nyc-taxi/schema"
}
assert duckdb_span["attributes"] == {
    "db.system.name": "duckdb",
    "ai.tool.name": "query_taxi_data",
    "ai.analysis": "top_pickup_zones",
    "ai.row_limit": 5,
}
PY

echo "Observability smoke passed"
echo "Application: ${web_url}"
echo "Jaeger: ${jaeger_url}"
echo "Trace run_id: ${run_id}"

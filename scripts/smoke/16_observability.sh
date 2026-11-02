#!/usr/bin/env bash
# Observability smoke (#221): an application ai.run trace reaches Phoenix through the OTEL Collector.
# Isolated Compose project and ephemeral host ports. The optional Jaeger overlay has its own check
# (16_observability_jaeger.sh).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ai-analytics-otel-smoke-$$}"
export WEB_PORT="${WEB_PORT:-0}"
export PHOENIX_PORT="${PHOENIX_PORT:-0}"
# Default Phoenix path: the Phoenix overlay turns AI_TRACE_RUN_IO on, so ai.run carries a bounded
# input.value / output.value. (The Jaeger overlay smoke covers the opposite: text absent.)
unset AI_TRACE_RUN_IO

compose=(
  docker compose
  -f docker-compose.yml
  -f docker-compose.phoenix.yml
  -f docker-compose.observability.yml
)

resolved_run_io="$("${compose[@]}" config | awk '/AI_TRACE_RUN_IO:/ {gsub(/"/, "", $2); print $2; exit}')"
if [[ "${resolved_run_io}" != "1" ]]; then
  echo "Resolved Compose config has AI_TRACE_RUN_IO=${resolved_run_io:-<unset>}; expected 1 on the Phoenix-only path" >&2
  exit 1
fi

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
phoenix_port="$("${compose[@]}" port phoenix 6006 | sed -E 's/.*:([0-9]+)$/\1/')"
web_url="http://127.0.0.1:${web_port}"
phoenix_url="http://127.0.0.1:${phoenix_port}"

echo "==> Waiting for application and Phoenix"
ready=0
for _ in $(seq 1 90); do
  if curl --silent --show-error --fail "${web_url}/api/status" >/dev/null 2>&1 \
    && curl --silent --show-error --fail "${phoenix_url}/healthz" >/dev/null 2>&1; then
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

echo "==> Waiting for distributed trace ${run_id} in Phoenix"
trace_found=0
spans_json=""
for _ in $(seq 1 90); do
  run_spans="$(curl --silent --fail --get "${phoenix_url}/v1/projects/default/spans" \
    --data-urlencode 'name=ai.run' --data-urlencode 'limit=100' || true)"
  trace_id="$(SPANS_JSON="${run_spans}" RUN_ID="${run_id}" python3 - <<'PY' || true
import json
import os

for span in json.loads(os.environ["SPANS_JSON"] or '{"data": []}')["data"]:
    if span["attributes"].get("ai.run_id") == os.environ["RUN_ID"]:
        print(span["context"]["trace_id"])
        raise SystemExit(0)
raise SystemExit(1)
PY
 )"
  if [[ -n "${trace_id}" ]]; then
    spans_json="$(curl --silent --fail --get "${phoenix_url}/v1/projects/default/spans" \
      --data-urlencode "trace_id=${trace_id}" --data-urlencode 'limit=1000' || true)"
    if SPANS_JSON="${spans_json}" python3 - <<'PY'
import json
import os

names = {span["name"] for span in json.loads(os.environ["SPANS_JSON"])["data"]}
required = {"ai.run", "mcp.request", "mcp.resource.read", "mcp.tool.execute", "duckdb.query"}
raise SystemExit(0 if required <= names else 1)
PY
    then
      trace_found=1
      break
    fi
  fi
  sleep 1
done

if [[ ${trace_found} -ne 1 ]]; then
  echo "Phoenix did not return the distributed trace for ${run_id}" >&2
  exit 1
fi

SPANS_JSON="${spans_json}" \
RUN_ID="${run_id}" \
CONVERSATION_ID="${conversation_id}" \
SMOKE_PROMPT="${smoke_prompt}" \
FAKE_DATASET_ROW_SENTINEL="${fake_dataset_row_sentinel}" \
python3 - <<'PY'
import json
import os

spans = json.loads(os.environ["SPANS_JSON"])["data"]
ai_run_ids = [s for s in spans if s["name"] == "ai.run"]
assert len(ai_run_ids) == 1
run_io = {
    key: ai_run_ids[0]["attributes"].get(key) for key in ("input.value", "output.value")
}
# Everything except the opt-in run input/output must stay free of private text.
for span in spans:
    for key in ("input.value", "output.value"):
        if span is ai_run_ids[0]:
            span["attributes"].pop(key, None)
raw = json.dumps(spans)
for sentinel in (
    os.environ["SMOKE_PROMPT"],
    os.environ["FAKE_DATASET_ROW_SENTINEL"],
    "SELECT secret_prompt_like_text FROM private_table",
    "aws_secret_access_key=not-a-real-credential",
):
    assert sentinel not in raw, f"private text reached the trace: {sentinel!r}"

# Phoenix-only default: ai.run carries the bounded question and answer.
assert run_io["input.value"] == os.environ["SMOKE_PROMPT"], run_io
assert run_io["output.value"] and len(run_io["output.value"]) <= 200, run_io
by_name = lambda name: [s for s in spans if s["name"] == name]

def only(items):
    items = list(items)
    assert len(items) == 1, items
    return items[0]

ai_run = only(s for s in by_name("ai.run") if s["attributes"].get("ai.run_id") == os.environ["RUN_ID"])
attrs = ai_run["attributes"]
assert attrs["ai.conversation_id"] == os.environ["CONVERSATION_ID"]
assert attrs["session.id"] == os.environ["CONVERSATION_ID"]
assert attrs["ai.status"] == "completed" and attrs["ai.turn_type"] == "text"
assert attrs["gen_ai.request.model"]

tool_span = only(by_name("mcp.tool.execute"))
duckdb_span = only(by_name("duckdb.query"))
resource_span = only(by_name("mcp.resource.read"))
requests = {s["context"]["span_id"]: s for s in by_name("mcp.request")}
tool_request = requests[tool_span["parent_id"]]
resource_request = requests[resource_span["parent_id"]]

assert tool_request["parent_id"] == ai_run["context"]["span_id"]
assert duckdb_span["parent_id"] == tool_span["context"]["span_id"]
assert resource_request["context"]["trace_id"] == ai_run["context"]["trace_id"]

assert {"rpc.system": "mcp", "rpc.method": "tools/call"}.items() <= tool_request["attributes"].items()
assert {"rpc.system": "mcp", "rpc.method": "resources/read"}.items() <= resource_request["attributes"].items()
assert tool_span["attributes"]["mcp.tool.name"] == "query_taxi_data"
assert resource_span["attributes"]["mcp.resource.uri"] == "dataset://nyc-taxi/schema"
assert {"db.system.name": "duckdb", "ai.tool.name": "query_taxi_data"}.items() <= duckdb_span["attributes"].items()
PY

echo "Observability smoke passed"
echo "Application: ${web_url}"
echo "Phoenix: ${phoenix_url}"
echo "Trace run_id: ${run_id}"

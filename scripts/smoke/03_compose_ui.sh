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

# Starts a run, optionally waits so the stream is read after completion, and
# prints the run's SSE event types in order, one per line.
run_event_types() {
  local prompt="$1" delay="${2:-0}" accepted run_id
  accepted="$(curl --silent --show-error --fail \
    --request POST "${web_url}/api/runs" \
    --header 'content-type: application/json' \
    --data "{\"prompt\":\"${prompt}\"}")"
  run_id="$(ACCEPTED="${accepted}" python -c \
    'import json, os; print(json.loads(os.environ["ACCEPTED"])["run_id"])')"
  sleep "${delay}"
  curl --silent --show-error --fail --max-time 30 \
    "${web_url}/api/runs/${run_id}/events" > "${sse_out}"
}

check_events() {
  SSE_FILE="${sse_out}" CASE="$1" python - <<'PY'
import json
import os

events = []
frames = open(os.environ["SSE_FILE"]).read().strip().split("\n\n")
for frame in frames:
    lines = frame.splitlines()
    kind = next((l[7:] for l in lines if l.startswith("event: ")), None)
    data = next((l[6:] for l in lines if l.startswith("data: ")), None)
    if kind and data:
        events.append((kind, json.loads(data)["payload"]))
types = [kind for kind, _ in events]
case = os.environ["CASE"]
assert types and types[-1] == "run.completed", (case, types)
if case == "tool":
    # Live events name the tool call tool.*; a durable rebuild names it step.tool_call.
    assert {"tool.completed", "step.tool_call"} & set(types), (case, types)
    assert "run.declined" not in types, (case, types)
else:
    assert not {"tool.requested", "step.tool_call"} & set(types), (case, types)
    assert types.count("run.declined") == 1, (case, types)
    assert types.index("run.declined") < types.index("run.completed"), (case, types)
    declined = dict(events)["run.declined"]
    assert declined["outcome"] == "unsupported" and declined["reason"], (case, declined)
PY
}

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
# Contract change (#115c catalogue): a catalogue-hit prompt skips the
# proposal LLM call entirely, so a run can be 1 call (catalogue hit,
# answer-only) or 2 calls (proposal + answer). `tool_source` distinguishing
# the two is internal RunStep/LLMCall metadata, not part of the AskResponse
# HTTP schema (see services/app/app/routers/ask.py), so this smoke only
# checks the resulting call-count range rather than the unexposed field.
assert 1 <= len(payload["llm_calls"]) <= 2
assert payload["usage"]["total_tokens"] > 0
PY
    # Run lifecycle over SSE with the fake provider (#186 follow-up).
    sse_out="$(mktemp)"
    run_event_types "Which pickup zones have the most trips?"
    echo "SSE check: tool question"
    check_events tool
    run_event_types "Delete all trips with zero distance."
    echo "SSE check: decline question"
    check_events decline
    # Regression: push the run-events stream past one XREAD page (50), then read a
    # finished decline run so it must be found live, not rebuilt from durable state.
    for _ in $(seq 1 15); do
      run_event_types "Which pickup zones have the most trips?"
    done
    run_event_types "Delete all trips with zero distance." 3
    echo "SSE check: decline after stream growth"
    check_events decline
    # Every catalogue prompt must complete against the real MCP service: the
    # catalogue path skips the model, so a failure here is a contract mismatch
    # between catalogue limits and the app/MCP layers (#186 det-006).
    catalogue_prompts="$(python - <<'PY'
import importlib.util
import json

spec = importlib.util.spec_from_file_location(
    "query_catalogue", "services/app/app/query_catalogue.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for key in module.QUERY_CATALOGUE:
    print(json.dumps(key)[1:-1])
PY
)"
    while IFS= read -r prompt; do
      run_event_types "${prompt}"
      echo "SSE check: catalogue prompt: ${prompt}"
      check_events tool
    done <<< "${catalogue_prompts}"
    # det-006 must come back complete: 2 week parts x 24 hours, no truncation.
    run_event_types "Compare weekday and weekend trip volume by hour"
    echo "SSE check: weekday/weekend by hour returns all 48 rows"
    SSE_FILE="${sse_out}" python - <<'PY'
import json
import os

reduced = None
for frame in open(os.environ["SSE_FILE"]).read().strip().split("\n\n"):
    lines = frame.splitlines()
    if "event: context.reduced" in lines:
        data = next(l[6:] for l in lines if l.startswith("data: "))
        reduced = json.loads(data)["payload"]
assert reduced is not None, "no context.reduced event"
assert reduced["row_count"] == 48, reduced["row_count"]
# The result columns lead the serialized working context, so they survive its bounding.
text = json.dumps(reduced)
assert "pickup_week_part" in text and "pickup_hour" in text, "week part / hour columns missing"
PY
    rm -f "${sse_out}"
    echo "Compose UI smoke passed"
    exit 0
  fi
  sleep 1
done

docker compose logs --no-color >&2
echo "Compose UI smoke timed out" >&2
exit 1

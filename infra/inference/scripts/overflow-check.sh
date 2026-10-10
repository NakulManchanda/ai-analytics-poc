#!/usr/bin/env bash
# Local, read-only check that the overflow destination speaks OpenAI-style chat (needs no cluster).
# Sends a few tiny requests (max_tokens 8) with SUPERLINKED_API_KEY; the key is never printed or put on a command line.
set -uo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: overflow-check.sh   (reads infra/inference/.env: SUPERLINKED_API_KEY, optional OVERFLOW_BASE_URL, OVERFLOW_URL, OVERFLOW_MODEL)"
  echo "Checks: GET /v1/models, chat, chat with tools, chat with stream. Exit 0 only if chat, tools and stream all pass."
  exit 0
fi

load_local_env
: "${SUPERLINKED_API_KEY:?SUPERLINKED_API_KEY is required (infra/inference/.env)}"
BASE="${OVERFLOW_BASE_URL:-https://api.superlinked.com}"
CHAT="$(normalize_chat_url "${OVERFLOW_URL:-$BASE}")"
MODEL="${OVERFLOW_MODEL:-Qwen/Qwen3.8-27B-FP8}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "base=$BASE chat=$CHAT model=$MODEL"

# Auth header comes from a process-substitution file descriptor, so the key is not visible in `ps`.
call() { # call <name> <method> <url> [json-body]
  local name="$1" method="$2" url="$3" body="${4:-}"
  local args=(-sS --max-time 180 -o "$TMP/$name.body" -w '%{http_code}' -X "$method" -H @<(printf 'authorization: Bearer %s' "$SUPERLINKED_API_KEY") -H 'content-type: application/json')
  [[ -n "$body" ]] && args+=(-d "$body")
  curl "${args[@]}" "$url" 2>"$TMP/$name.err" || true
}
show() { [[ -f "$TMP/$1.body" ]] && { head -c 300 "$TMP/$1.body" | tr '\n' ' '; echo; }; [[ -s "$TMP/$1.err" ]] && head -c 200 "$TMP/$1.err"; true; }

fail=0
echo "== 1. GET /v1/models (informational) =="
code=$(call models GET "$BASE/v1/models"); echo "http $code"
if [[ "$code" == "200" ]]; then
  python3 -I - "$TMP/models.body" "$MODEL" <<'PY'
import json, sys
try:
    ids = [m.get("id") for m in json.load(open(sys.argv[1])).get("data", [])]
except Exception as exc:
    print("unparseable models body:", type(exc).__name__); sys.exit(0)
print("model listed:", sys.argv[2] in ids, "| models returned:", len(ids))
PY
else show models; fi

echo "== 2. chat completion =="
code=$(call chat POST "$CHAT" "{\"model\":\"$MODEL\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"Say hi.\"}]}")
echo "http $code"
python3 -I - "$TMP/chat.body" <<'PY' || fail=1
import json, sys
try:
    d = json.load(open(sys.argv[1])); msg = d["choices"][0]["message"]; u = d.get("usage")
except Exception as exc:
    print("FAIL: not an OpenAI-style chat response:", type(exc).__name__); sys.exit(1)
print("OK: content=%r usage=%s" % ((msg.get("content") or "")[:40], u))
PY
[[ "$code" == "200" ]] || { fail=1; show chat; }
[[ "$code" == "401" ]] && echo "HINT: 401 = the key is rejected. Check the console (Developers > API keys) that SUPERLINKED_API_KEY is an ACTIVE key (compare its last 4 characters), then run make inference-secret and restart the gateway."

echo "== 3. chat with tools =="
TOOLS='[{"type":"function","function":{"name":"get_trip_count","description":"Count taxi trips for a zone","parameters":{"type":"object","properties":{"zone":{"type":"string"}},"required":["zone"]}}}]'
code=$(call tools POST "$CHAT" "{\"model\":\"$MODEL\",\"max_tokens\":64,\"tool_choice\":\"auto\",\"tools\":$TOOLS,\"messages\":[{\"role\":\"user\",\"content\":\"How many taxi trips started in Midtown? Use the tool.\"}]}")
echo "http $code"
python3 -I - "$TMP/tools.body" <<'PY' || fail=1
import json, sys
try:
    msg = json.load(open(sys.argv[1]))["choices"][0]["message"]
except Exception as exc:
    print("FAIL: not an OpenAI-style response:", type(exc).__name__); sys.exit(1)
calls = msg.get("tool_calls") or []
print("OK: tool_calls returned:", len(calls)) if calls else print("FAIL: accepted `tools` but returned no tool call (agent loop would break on overflow)")
sys.exit(0 if calls else 1)
PY
[[ "$code" == "200" ]] || { fail=1; show tools; }

echo "== 4. chat with stream =="
code=$(call stream POST "$CHAT" "{\"model\":\"$MODEL\",\"max_tokens\":8,\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Say hi.\"}]}")
echo "http $code"
if [[ "$code" == "200" ]] && grep -q '^data:' "$TMP/stream.body" && grep -q 'data: \[DONE\]' "$TMP/stream.body"; then
  echo "OK: SSE data chunks and [DONE] received"
else
  echo "FAIL: no SSE stream with [DONE]"; show stream; fail=1
fi

echo
[[ $fail -eq 0 ]] && echo "RESULT: overflow destination OK (chat, tools, stream). Set OVERFLOW_URL=$CHAT and run make inference-overflow-on." \
  || echo "RESULT: NOT ready. Do not enable overflow; an adapter in gateway/main.py _overflow may be needed."
exit $fail

#!/usr/bin/env bash
# Smoke: a real run through the app is intercepted and logged by the local LiteLLM proxy.
# Targets the running `make local-aws-litellm-compose` stack. Spends a few Bedrock tokens.
set -euo pipefail

web="http://localhost:${WEB_PORT:-3000}"
proxy="http://127.0.0.1:${LITELLM_PORT:-14000}"
set -a; [[ -f .env ]] && . ./.env; set +a
key="${LITELLM_MASTER_KEY:?run make litellm-env first}"
prompt="${1:-Show an unusual ranking of pickup spots, please}"

run="$(curl -sf -X POST "${web}/api/runs" -H 'content-type: application/json' \
  -d "{\"prompt\":\"${prompt}\"}")"
read -r cid rid < <(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["conversation_id"], d["run_id"])' "${run}")
echo "run ${rid} conversation ${cid}"

events="$(curl -s --max-time 60 "${web}/api/runs/${rid}/events" || true)"
if grep -q "run.completed" <<<"${events}"; then echo "run: completed"; else echo "run: NOT completed" >&2; exit 1; fi

# The proxy batches spend-log writes; poll until the session's calls appear.
for _ in $(seq 1 24); do
  found="$(curl -sf "${proxy}/spend/logs" -H "Authorization: Bearer ${key}" \
    | jq -r --arg s "${cid}" '[.[] | select(.session_id == $s)] | sort_by(.startTime)[]
        | "\(.startTime[11:19]) \(.model_group) \(.status) \(.total_tokens) tokens"' || true)"
  if grep -q "taxi-answer" <<<"${found}"; then
    echo "proxy logged calls for session ${cid}:"; echo "${found}"
    echo "find them: ${proxy}/ui -> Logs (session ${cid}), or make litellm-calls SESSION=${cid}"
    exit 0
  fi
  sleep 5
done
echo "proxy did not log the run's calls for session ${cid}" >&2
exit 1

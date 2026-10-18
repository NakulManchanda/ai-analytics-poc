#!/usr/bin/env bash
# Smoke: a real run through the app is intercepted and logged by the local LiteLLM proxy.
# Targets the running `make local-aws-litellm-compose` stack. Spends a few Bedrock tokens.
set -euo pipefail

web="http://localhost:${WEB_PORT:-3000}"
proxy="http://127.0.0.1:${LITELLM_PORT:-14000}"
key="${LITELLM_MASTER_KEY:-sk-local-dev}"
prompt="${1:-Show an unusual ranking of pickup spots, please}"

run="$(curl -sf -X POST "${web}/api/runs" -H 'content-type: application/json' \
  -d "{\"prompt\":\"${prompt}\"}")"
read -r cid rid < <(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["conversation_id"], d["run_id"])' "${run}")
echo "run ${rid} conversation ${cid}"

events="$(curl -s --max-time 60 "${web}/api/runs/${rid}/events" || true)"
if grep -q "run.completed" <<<"${events}"; then echo "run: completed"; else echo "run: NOT completed" >&2; exit 1; fi

# The proxy batches spend-log writes; poll until the session's calls appear.
for _ in $(seq 1 24); do
  rows="$(curl -sf "${proxy}/spend/logs" -H "Authorization: Bearer ${key}" || echo '[]')"
  found="$(python3 -c '
import json,sys
rows=[r for r in json.loads(sys.argv[1]) if r.get("session_id")==sys.argv[2]]
for r in sorted(rows,key=lambda r:r["startTime"]):
    print(r["startTime"][11:19], r["model_group"], r["status"], r["total_tokens"], "tokens")
' "${rows}" "${cid}")"
  if [[ -n "${found}" ]] && grep -q "taxi-answer" <<<"${found}"; then
    echo "proxy logged calls for session ${cid}:"; echo "${found}"
    echo "find them: ${proxy}/ui -> Logs (session ${cid}), or GET ${proxy}/spend/logs"
    exit 0
  fi
  sleep 5
done
echo "proxy did not log the run's calls for session ${cid}" >&2
exit 1

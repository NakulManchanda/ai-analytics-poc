#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: smoke.sh [WORKER_A_PORT] [WORKER_B_PORT]"
  exit 0
fi

load_local_env

WORKER_A_PORT="${1:-18001}"
WORKER_B_PORT="${2:-18002}"
MODEL="${INFERENCE_MODEL:-Qwen/Qwen3-0.6B}"

echo "== Smoking Worker A on port $WORKER_A_PORT =="
curl --fail --silent "http://127.0.0.1:$WORKER_A_PORT/health" >/dev/null
curl --fail --silent "http://127.0.0.1:$WORKER_A_PORT/v1/models" >/dev/null
curl --fail --silent "http://127.0.0.1:$WORKER_A_PORT/metrics" >/dev/null
curl --fail --silent -X POST "http://127.0.0.1:$WORKER_A_PORT/v1/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"$MODEL\", \"prompt\": \"Hello\", \"max_tokens\": 5}" >/dev/null
echo "Worker A passed health, model list, metrics, and completion."

echo "== Smoking Worker B on port $WORKER_B_PORT =="
curl --fail --silent "http://127.0.0.1:$WORKER_B_PORT/health" >/dev/null
curl --fail --silent "http://127.0.0.1:$WORKER_B_PORT/v1/models" >/dev/null
curl --fail --silent "http://127.0.0.1:$WORKER_B_PORT/metrics" >/dev/null
curl --fail --silent -X POST "http://127.0.0.1:$WORKER_B_PORT/v1/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"$MODEL\", \"prompt\": \"Hello\", \"max_tokens\": 5}" >/dev/null
echo "Worker B passed health, model list, metrics, and completion."

if [[ -n "${LAMBDA_SSH_HOST:-}" ]]; then
  echo "== Running negative check against direct reachability on $LAMBDA_SSH_HOST =="
  for port in 8000 8001 8002 3000; do
    if curl --connect-timeout 2 --max-time 3 --silent "http://$LAMBDA_SSH_HOST:$port" >/dev/null 2>&1; then
      echo "NEGATIVE CHECK FAILED: port $port on $LAMBDA_SSH_HOST is publicly reachable!" >&2
      exit 1
    fi
  done
  echo "Negative check passed: ports are unreachable without SSH tunnel."
fi

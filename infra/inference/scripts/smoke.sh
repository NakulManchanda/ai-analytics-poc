#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: smoke.sh [WORKER_A_PORT] [WORKER_B_PORT] [OUTPUT_DIR]"
  exit 0
fi

load_local_env

WORKER_A_PORT="${1:-18001}"
WORKER_B_PORT="${2:-18002}"
OUTPUT_DIR="${3:-}"
MODEL="${INFERENCE_MODEL:-Qwen/Qwen3-0.6B}"

EXTRA_ARGS=()
if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR/raw"
  EXTRA_ARGS=("--output-file" "$OUTPUT_DIR/raw/responses.jsonl")
fi

echo "== Probing Worker A on port $WORKER_A_PORT =="
python3 "$INFERENCE_ROOT/experiments/probe.py" \
  --base-url "http://127.0.0.1:$WORKER_A_PORT" \
  --model "$MODEL" \
  --prompt "Ready worker A." \
  --max-tokens 5 \
  "${EXTRA_ARGS[@]}"
echo "Worker A verified successfully."

echo "== Probing Worker B on port $WORKER_B_PORT =="
python3 "$INFERENCE_ROOT/experiments/probe.py" \
  --base-url "http://127.0.0.1:$WORKER_B_PORT" \
  --model "$MODEL" \
  --prompt "Ready worker B." \
  --max-tokens 5 \
  "${EXTRA_ARGS[@]}"
echo "Worker B verified successfully."

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

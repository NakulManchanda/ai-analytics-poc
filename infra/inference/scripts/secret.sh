#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: secret.sh (streams optional HF_TOKEN and SUPERLINKED_API_KEY over SSH stdin; idempotent)"; exit 0; fi
load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"
if [[ -n "${HF_TOKEN:-}" ]]; then
  printf '%s' "$HF_TOKEN" | ssh_cmd "kubectl -n '$INFERENCE_NAMESPACE' create secret generic hf-token --from-file=token=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -"
  echo "hf-token applied"
else
  echo "HF_TOKEN is optional; skipped"
fi
# Overflow destination key (C6/C8 only). The gateway reads it from this Secret once its pod starts, so
# restart the gateway after creating it on a running cluster.
if [[ -n "${SUPERLINKED_API_KEY:-}" ]]; then
  printf '%s' "$SUPERLINKED_API_KEY" | ssh_cmd "kubectl -n '$INFERENCE_NAMESPACE' create secret generic overflow-credentials --from-file=api-key=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -"
  echo "overflow-credentials applied"
else
  echo "SUPERLINKED_API_KEY is optional; skipped"
fi

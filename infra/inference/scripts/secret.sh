#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: secret.sh (streams optional HF_TOKEN on stdin)"; exit 0; fi
load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"
[[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN is optional; nothing to stream"; exit 0; }
printf '%s' "$HF_TOKEN" | ssh_cmd "kubectl -n '$INFERENCE_NAMESPACE' create secret generic hf-token --from-file=token=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -"

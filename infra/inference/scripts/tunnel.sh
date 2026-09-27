#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: tunnel.sh"; exit 0; fi
load_local_env; require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"
ssh -i "$LAMBDA_SSH_KEY_PATH" -N \
  -L 18001:127.0.0.1:8001 -L 18002:127.0.0.1:8002 -L 13000:127.0.0.1:3000 \
  "$(ssh_target)" "
    kubectl -n '$INFERENCE_NAMESPACE' port-forward --address 127.0.0.1 svc/inference-worker-a 8001:8000 &
    kubectl -n '$INFERENCE_NAMESPACE' port-forward --address 127.0.0.1 svc/inference-worker-b 8002:8000 &
    kubectl -n '$INFERENCE_NAMESPACE' port-forward --address 127.0.0.1 svc/grafana 3000:80 &
    wait
  "

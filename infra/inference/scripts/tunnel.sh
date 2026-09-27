#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: tunnel.sh"; exit 0; fi
load_local_env; require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

echo "Opening loopback SSH tunnel to Lambda host..."
echo "  - http://127.0.0.1:18001 -> Worker A (vLLM)"
echo "  - http://127.0.0.1:18002 -> Worker B (vLLM)"
echo "  - http://127.0.0.1:13000 -> Grafana"

exec ssh -i "$LAMBDA_SSH_KEY_PATH" \
  -o StrictHostKeyChecking=accept-new \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=6 \
  -L 18001:127.0.0.1:8001 -L 18002:127.0.0.1:8002 -L 13000:127.0.0.1:3000 \
  "$(ssh_target)" "
    trap 'kill 0' EXIT INT TERM
    echo 'Remote port forwards active for inference-worker-a, inference-worker-b, and grafana.'
    kubectl -n '$INFERENCE_NAMESPACE' port-forward --address 127.0.0.1 svc/inference-worker-a 8001:8000 &
    kubectl -n '$INFERENCE_NAMESPACE' port-forward --address 127.0.0.1 svc/inference-worker-b 8002:8000 &
    kubectl -n '$INFERENCE_NAMESPACE' port-forward --address 127.0.0.1 svc/grafana 3000:80 &
    wait
  "

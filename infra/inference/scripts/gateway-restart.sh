#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: gateway-restart.sh"
  exit 0
fi

load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

# Sync latest gateway definitions to remote
bash "$(dirname "$0")/sync.sh"

ssh_cmd "
  set -eu
  export KUBECONFIG=\${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}
  kubectl -n '$INFERENCE_NAMESPACE' create configmap inference-gateway-code \
    --from-file=$(remote_dir)/gateway \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n '$INFERENCE_NAMESPACE' rollout restart deploy/inference-gateway
  kubectl -n '$INFERENCE_NAMESPACE' rollout status deploy/inference-gateway --timeout=2m
  echo '== Gateway restarted successfully =='
"

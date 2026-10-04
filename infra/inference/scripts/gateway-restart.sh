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

# Sync only the gateway code (not the whole tree, so remote evidence and other files are untouched)
RDIR="$(remote_dir)"
ssh_cmd "mkdir -p $RDIR/gateway"
rsync -az --delete -e "ssh -i $LAMBDA_SSH_KEY_PATH -o StrictHostKeyChecking=accept-new" \
  "$INFERENCE_ROOT/gateway/" "$(ssh_target):$RDIR/gateway/"

ssh_cmd "
  set -eu
  export KUBECONFIG=\${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}
  RDIR=$RDIR
  kubectl -n '$INFERENCE_NAMESPACE' create configmap inference-gateway-code \
    --from-file=\"\$RDIR/gateway\" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n '$INFERENCE_NAMESPACE' rollout restart deploy/inference-gateway
  kubectl -n '$INFERENCE_NAMESPACE' rollout status deploy/inference-gateway --timeout=2m
  echo '== Gateway restarted successfully =='
"

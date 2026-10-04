#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" || ( "${1:-}" != "on" && "${1:-}" != "off" ) ]]; then
  echo "Usage: controls.sh on|off"
  echo "on : ALLOW_EXPERIMENT_CONTROLS=1 and the E4 TENANT_ALLOWLIST on the gateway (test-only; restarts the pod)"
  echo "off: ALLOW_EXPERIMENT_CONTROLS=0 and remove ALLOW_FORCED_PLACEMENT"
  [[ "${1:-}" == "--help" ]] && exit 0 || exit 2
fi

load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

if [[ "$1" == "on" ]]; then
  ENV_ARGS="ALLOW_EXPERIMENT_CONTROLS=1 TENANT_ALLOWLIST=tenant_interactive,tenant_noisy,tenant_batch"
else
  ENV_ARGS="ALLOW_EXPERIMENT_CONTROLS=0 ALLOW_FORCED_PLACEMENT-"
fi

ssh_cmd "
  set -eu
  export KUBECONFIG=\${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}
  kubectl -n '$INFERENCE_NAMESPACE' set env deploy/inference-gateway $ENV_ARGS
  kubectl -n '$INFERENCE_NAMESPACE' rollout status deploy/inference-gateway --timeout=2m
  echo '== Gateway experiment controls: $1 =='
"

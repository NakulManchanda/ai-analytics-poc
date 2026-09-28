#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: teardown.sh"; exit 0; fi
load_local_env; require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"
ssh_cmd "
  export KUBECONFIG=\"\${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}\"
  helm uninstall grafana --namespace '$INFERENCE_NAMESPACE' 2>/dev/null || true
  helm uninstall prometheus --namespace '$INFERENCE_NAMESPACE' 2>/dev/null || true
  kubectl delete namespace '$INFERENCE_NAMESPACE' --ignore-not-found
  helm uninstall hami --namespace kube-system 2>/dev/null || true
"

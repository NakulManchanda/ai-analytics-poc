#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" || ( "${1:-}" != "on" && "${1:-}" != "off" ) ]]; then
  echo "Usage: overflow.sh on|off"
  echo "on : enable overflow on the gateway (restarts the pod). Needs OVERFLOW_URL in infra/inference/.env or the environment;"
  echo "     OVERFLOW_MODEL (default Qwen/Qwen3.8-27B-FP8) and OVERFLOW_PROVIDER (default superlinked) are optional."
  echo "     The key comes from the overflow-credentials Secret (make inference-secret)."
  echo "off: OVERFLOW_ENABLED=0 (default). Keep it off for E4/E5: overflow would turn their 503s into overflowed requests."
  [[ "${1:-}" == "--help" ]] && exit 0 || exit 2
fi

load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

if [[ "$1" == "on" ]]; then
  : "${OVERFLOW_URL:?OVERFLOW_URL is required (OpenAI-compatible .../v1/chat/completions)}"
  OVERFLOW_URL="$(normalize_chat_url "$OVERFLOW_URL")"   # the gateway posts to this exact URL
  ENV_ARGS="OVERFLOW_ENABLED=1 OVERFLOW_PROVIDER='${OVERFLOW_PROVIDER:-superlinked}' OVERFLOW_MODEL='${OVERFLOW_MODEL:-Qwen/Qwen3.8-27B-FP8}' OVERFLOW_URL='$OVERFLOW_URL'"
else
  ENV_ARGS="OVERFLOW_ENABLED=0"
fi

ssh_cmd "
  set -eu
  export KUBECONFIG=\${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}
  kubectl -n '$INFERENCE_NAMESPACE' get secret overflow-credentials >/dev/null 2>&1 || echo 'WARNING: overflow-credentials Secret missing (run make inference-secret); requests would go out unauthenticated'
  kubectl -n '$INFERENCE_NAMESPACE' set env deploy/inference-gateway $ENV_ARGS
  kubectl -n '$INFERENCE_NAMESPACE' rollout status deploy/inference-gateway --timeout=2m
  echo '== Gateway overflow: $1 =='
"

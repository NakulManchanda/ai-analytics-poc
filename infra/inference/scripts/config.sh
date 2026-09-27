#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
safe_keys=(INFERENCE_NAMESPACE INFERENCE_MODEL INFERENCE_MODEL_REVISION INFERENCE_VLLM_IMAGE INFERENCE_K3S_VERSION INFERENCE_HELM_VERSION INFERENCE_HAMI_VERSION)
if [[ "${1:-}" == "--help" ]]; then echo "Usage: config.sh [--print-keys]"; exit 0; fi
if [[ "${1:-}" == "--print-keys" ]]; then printf '%s\n' "${safe_keys[@]}"; exit 0; fi
load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

python3 -c '
import json, os, sys
keys = sys.argv[1:]
namespace = os.environ.get("INFERENCE_NAMESPACE", "inference-lab")
data = {k: os.environ.get(k, "") for k in keys}
manifest = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {
        "name": "inference-config",
        "namespace": namespace,
    },
    "data": data,
}
print(json.dumps(manifest))
' "${safe_keys[@]}" | ssh_cmd "kubectl create namespace '$INFERENCE_NAMESPACE' --dry-run=client -o yaml | kubectl apply -f - && kubectl apply -f -"

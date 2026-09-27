#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: pull-evidence.sh [RUN_ID]"
  echo "Captures remote cluster status, worker logs, DCGM metrics, and pulls into metrics/inference/<run_id>"
  exit 0
fi

load_local_env
require_connection

RUN_ID="${1:-run-$(date +%Y%m%d_%H%M%S)}"
: "${INFERENCE_NAMESPACE:=inference-lab}"
LOCAL_EVIDENCE_DIR="$INFERENCE_ROOT/../../metrics/inference/$RUN_ID"
mkdir -p "$LOCAL_EVIDENCE_DIR"

echo "== Capturing remote cluster evidence for run: $RUN_ID =="
ssh_cmd "
  set -eu
  EVID_DIR=\"$(remote_dir)/evidence/$RUN_ID\"
  mkdir -p \"\$EVID_DIR/kubectl\" \"\$EVID_DIR/logs\" \"\$EVID_DIR/hardware\" \"\$EVID_DIR/prometheus\"

  # 1. Kubernetes resource status
  kubectl get pods -n '$INFERENCE_NAMESPACE' -o json > \"\$EVID_DIR/kubectl/pods.json\" 2>/dev/null || true
  kubectl get deploy -n '$INFERENCE_NAMESPACE' -o json > \"\$EVID_DIR/kubectl/deployments.json\" 2>/dev/null || true
  kubectl get svc -n '$INFERENCE_NAMESPACE' -o json > \"\$EVID_DIR/kubectl/services.json\" 2>/dev/null || true

  # 2. Worker and observability logs
  kubectl logs -n '$INFERENCE_NAMESPACE' -l app=inference-worker-a --tail=1000 > \"\$EVID_DIR/logs/worker-a.log\" 2>/dev/null || true
  kubectl logs -n '$INFERENCE_NAMESPACE' -l app=inference-worker-b --tail=1000 > \"\$EVID_DIR/logs/worker-b.log\" 2>/dev/null || true
  kubectl logs -n '$INFERENCE_NAMESPACE' -l app=dcgm-exporter --tail=500 > \"\$EVID_DIR/logs/dcgm-exporter.log\" 2>/dev/null || true

  # 3. GPU hardware state snapshot
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu --format=csv > \"\$EVID_DIR/hardware/nvidia-smi.csv\" 2>/dev/null || true
  fi
"

echo "== Pulling evidence to $LOCAL_EVIDENCE_DIR =="
rsync -az -e "ssh -i $LAMBDA_SSH_KEY_PATH -o StrictHostKeyChecking=accept-new" \
  "$(ssh_target):$(remote_dir)/evidence/$RUN_ID/" "$LOCAL_EVIDENCE_DIR/"

echo "Evidence successfully pulled to $LOCAL_EVIDENCE_DIR"

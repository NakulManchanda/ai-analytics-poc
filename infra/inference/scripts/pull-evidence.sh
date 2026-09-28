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
  export KUBECONFIG=\"\${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}\"
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
    nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu --format=csv > "\$EVID_DIR/hardware/nvidia-smi.csv" 2>/dev/null || true
  fi
  kubectl exec -n '$INFERENCE_NAMESPACE' deployment/inference-worker-a -- nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv > "\$EVID_DIR/hardware/pod-worker-a-nvidia-smi.csv" 2>/dev/null || true
  kubectl exec -n '$INFERENCE_NAMESPACE' deployment/inference-worker-b -- nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv > "\$EVID_DIR/hardware/pod-worker-b-nvidia-smi.csv" 2>/dev/null || true

  # 4. Scrapes for vLLM, DCGM, and Prometheus
  WORKER_A_IP=\$(kubectl get svc -n '$INFERENCE_NAMESPACE' inference-worker-a -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo '')
  if [[ -n \"\$WORKER_A_IP\" ]]; then
    curl -s --connect-timeout 5 \"http://\$WORKER_A_IP:8000/metrics\" > \"\$EVID_DIR/prometheus/vllm-worker-a.prom\" 2>/dev/null || true
  fi
  WORKER_B_IP=\$(kubectl get svc -n '$INFERENCE_NAMESPACE' inference-worker-b -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo '')
  if [[ -n \"\$WORKER_B_IP\" ]]; then
    curl -s --connect-timeout 5 \"http://\$WORKER_B_IP:8000/metrics\" > \"\$EVID_DIR/prometheus/vllm-worker-b.prom\" 2>/dev/null || true
  fi
  DCGM_IP=\$(kubectl get svc -n '$INFERENCE_NAMESPACE' dcgm-exporter -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo '')
  if [[ -n \"\$DCGM_IP\" ]]; then
    curl -s --connect-timeout 5 \"http://\$DCGM_IP:9400/metrics\" > \"\$EVID_DIR/prometheus/dcgm.prom\" 2>/dev/null || true
  fi
  PROM_IP=\$(kubectl get svc -n '$INFERENCE_NAMESPACE' prometheus-server -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo '')
  if [[ -n \"\$PROM_IP\" ]]; then
    curl -s --connect-timeout 5 \"http://\$PROM_IP:80/metrics\" > \"\$EVID_DIR/prometheus/prometheus-server.prom\" 2>/dev/null || true
  fi
"

echo "== Pulling evidence to $LOCAL_EVIDENCE_DIR =="
rsync -az -e "ssh -i $LAMBDA_SSH_KEY_PATH -o StrictHostKeyChecking=accept-new" \
  "$(ssh_target):$(remote_dir)/evidence/$RUN_ID/" "$LOCAL_EVIDENCE_DIR/"

for required in \
  "$LOCAL_EVIDENCE_DIR/kubectl/pods.json" \
  "$LOCAL_EVIDENCE_DIR/logs/worker-a.log" \
  "$LOCAL_EVIDENCE_DIR/prometheus/vllm-worker-a.prom" \
  "$LOCAL_EVIDENCE_DIR/prometheus/dcgm.prom" \
  "$LOCAL_EVIDENCE_DIR/hardware/pod-worker-a-nvidia-smi.csv"; do
  if [[ ! -s "$required" ]]; then
    echo "Error: Required evidence artifact $required is missing or empty" >&2
    exit 1
  fi
done

if [[ -f "$INFERENCE_ROOT/experiments/evidence.py" ]]; then
  python3 "$INFERENCE_ROOT/experiments/evidence.py" --run-id "$RUN_ID" --output-dir "$LOCAL_EVIDENCE_DIR"
fi

echo "Evidence successfully pulled and verified in $LOCAL_EVIDENCE_DIR"

#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: deploy.sh"; exit 0; fi
load_local_env; require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

ssh_cmd "
  set -eu
  kubectl apply -f '$(remote_dir)/k8s/namespace.yaml'
  kubectl apply -R -f '$(remote_dir)/k8s'
  kubectl apply -f '$(remote_dir)/observability/dcgm/dcgm-exporter.yaml'
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update prometheus-community >/dev/null 2>&1 || true
  helm upgrade --install prometheus prometheus-community/prometheus \
    --namespace '$INFERENCE_NAMESPACE' \
    --version '25.27.0' \
    -f '$(remote_dir)/observability/prometheus/values.yaml' \
    --wait --timeout 10m
  helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update grafana >/dev/null 2>&1 || true
  helm upgrade --install grafana grafana/grafana \
    --namespace '$INFERENCE_NAMESPACE' \
    --version '8.5.1' \
    -f '$(remote_dir)/observability/grafana/values.yaml' \
    --wait --timeout 10m
  kubectl -n '$INFERENCE_NAMESPACE' rollout status deploy/inference-worker-a --timeout=15m
  kubectl -n '$INFERENCE_NAMESPACE' rollout status deploy/inference-worker-b --timeout=15m
"

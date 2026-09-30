#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: bootstrap.sh"; exit 0; fi
load_local_env; require_connection
: "${INFERENCE_K3S_VERSION:=v1.37.0+k3s1}"; : "${INFERENCE_HELM_VERSION:=v3.22.0}"; : "${INFERENCE_HAMI_VERSION:=2.9.0}"

ssh_cmd "
  set -eu
  command -v nvidia-smi >/dev/null 2>&1 || { echo 'nvidia-smi not found on host' >&2; exit 1; }

  if [[ ! -x /usr/local/bin/k3s && ! -x /usr/bin/k3s ]]; then
    echo 'Installing k3s $INFERENCE_K3S_VERSION...'
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION='$INFERENCE_K3S_VERSION' INSTALL_K3S_EXEC='--write-kubeconfig-mode 644 --default-runtime nvidia' sh -
  fi

  export KUBECONFIG='/etc/rancher/k3s/k3s.yaml'
  mkdir -p ~/.kube && cp -f /etc/rancher/k3s/k3s.yaml ~/.kube/config 2>/dev/null && chmod 600 ~/.kube/config 2>/dev/null || true
  for i in \$(seq 1 30); do
    if kubectl get nodes -o name 2>/dev/null | grep -q 'node/'; then
      break
    fi
    sleep 2
  done
  kubectl wait --for=condition=Ready node --all --timeout=60s

  CURRENT_HELM=""
  if command -v helm >/dev/null 2>&1; then
    CURRENT_HELM=\$(helm version --template '{{.Version}}' 2>/dev/null || helm version --short 2>/dev/null || true)
  fi
  if [[ "\$CURRENT_HELM" != *"$INFERENCE_HELM_VERSION"* ]]; then
    echo "Installing helm $INFERENCE_HELM_VERSION..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | DESIRED_VERSION='$INFERENCE_HELM_VERSION' bash
  fi
  HELM_VER=\$(helm version --template '{{.Version}}' 2>/dev/null || helm version --short 2>/dev/null || echo 'unknown')
  echo "Helm version verified: \$HELM_VER"

  NODE=\$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
  kubectl label node \"\$NODE\" gpu=on --overwrite

  helm repo add hami-charts https://project-hami.github.io/HAMi/ >/dev/null 2>&1 || true
  helm repo update hami-charts >/dev/null 2>&1 || true

  K8S_VERSION=\$(kubectl version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"serverVersion\"][\"gitVersion\"].split(\"+\")[0])')
  echo \"Deploying HAMi $INFERENCE_HAMI_VERSION aligned with Kubernetes \$K8S_VERSION...\"

  helm upgrade --install hami hami-charts/hami \
    --version '$INFERENCE_HAMI_VERSION' \
    --namespace kube-system \
    --set scheduler.kubeScheduler.image.registry=registry.k8s.io \
    --set scheduler.kubeScheduler.image.repository=kube-scheduler \
    --set \"scheduler.kubeScheduler.image.tag=\${K8S_VERSION}\" \
    --set \"scheduler.kubeScheduler.imageTag=\${K8S_VERSION}\" \
    --set devicePlugin.deviceSplitCount=2 \
    --wait --timeout 10m
"

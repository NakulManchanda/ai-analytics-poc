#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: bootstrap.sh"; exit 0; fi
load_local_env; require_connection
: "${INFERENCE_K3S_VERSION:=v1.37.0+k3s1}"; : "${INFERENCE_HELM_VERSION:=v3.22.0}"; : "${INFERENCE_HAMI_VERSION:=2.9.0}"
ssh_cmd "set -eu; command -v nvidia-smi; curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION='$INFERENCE_K3S_VERSION' INSTALL_K3S_EXEC='--write-kubeconfig-mode 644 --default-runtime nvidia' sh -; if ! command -v helm >/dev/null; then curl -fsSL https://get.helm.sh/helm-$INFERENCE_HELM_VERSION-linux-amd64.tar.gz | tar -xz --strip-components=1 -C /usr/local/bin linux-amd64/helm; fi; helm repo add hami-charts https://project-hami.github.io/HAMi/ >/dev/null; helm repo update >/dev/null; helm upgrade --install hami hami-charts/hami --version '$INFERENCE_HAMI_VERSION' --namespace kube-system --set devicePlugin.deviceSplitCount=2 --wait --timeout 10m"

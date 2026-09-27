#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" ]]; then echo "Usage: validate.sh"; exit 0; fi
root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="$(cd "$root/../.." && pwd)"
if [[ -f "$repo_root/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$repo_root/.venv/bin/activate"
fi

echo "Validating Kubernetes and observability YAML manifests..."
find "$root/k8s" "$root/observability" \( -name '*.yaml' -o -name '*.yml' \) -print0 | xargs -0 -n1 python3 -c '
import sys, yaml
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    docs = list(yaml.safe_load_all(f))
    assert docs, f"Empty YAML file: {path}"
'

echo "Validating Grafana dashboard JSON..."
if [[ -d "$root/observability/grafana/dashboards" ]]; then
  find "$root/observability/grafana/dashboards" -name '*.json' -print0 | xargs -0 -n1 python3 -c '
import sys, json
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
    assert isinstance(data, dict) and "panels" in data, f"Invalid Grafana dashboard: {path}"
'
fi

echo "All manifests and dashboards validated successfully."

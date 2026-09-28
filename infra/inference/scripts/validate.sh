#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" ]]; then echo "Usage: validate.sh"; exit 0; fi
root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="$(cd "$root/../.." && pwd)"
if command -v uv >/dev/null 2>&1 && [[ -d "$repo_root/services/app" ]]; then
  PYTHON_RUN=(uv run --project "$repo_root/services/app" python)
elif [[ -f "$repo_root/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$repo_root/.venv/bin/activate"
  PYTHON_RUN=(python3)
else
  PYTHON_RUN=(python3)
fi

echo "Validating Kubernetes and observability YAML manifests..."
find "$root/k8s" "$root/observability" \( -name '*.yaml' -o -name '*.yml' \) -print0 | xargs -0 "${PYTHON_RUN[@]}" -c '
import sys, yaml
for path in sys.argv[1:]:
    with open(path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
        assert docs, f"Empty YAML file: {path}"
'

echo "Validating Grafana dashboard JSON..."
if [[ -d "$root/observability/grafana/dashboards" ]]; then
  find "$root/observability/grafana/dashboards" -name '*.json' -print0 | xargs -0 "${PYTHON_RUN[@]}" -c '
import sys, json
for path in sys.argv[1:]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert isinstance(data, dict) and "panels" in data, f"Invalid Grafana dashboard: {path}"
'
fi

echo "All manifests and dashboards validated successfully."

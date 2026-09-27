#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" ]]; then echo "Usage: validate.sh"; exit 0; fi
root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="$(cd "$root/../.." && pwd)"
if [[ -f "$repo_root/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$repo_root/.venv/bin/activate"
fi
find "$root/k8s" "$root/observability" -name '*.yaml' -o -name '*.yml' -print0 | xargs -0 -n1 python3 -c 'import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))'

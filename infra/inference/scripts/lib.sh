#!/usr/bin/env bash
set -euo pipefail

INFERENCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFERENCE_ENV_FILE="${INFERENCE_ENV_FILE:-$INFERENCE_ROOT/.env}"

load_local_env() {
  if [[ -f "$INFERENCE_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$INFERENCE_ENV_FILE"
    set +a
  fi
}

require_connection() {
  : "${LAMBDA_SSH_HOST:?LAMBDA_SSH_HOST is required}"
  : "${LAMBDA_SSH_USER:?LAMBDA_SSH_USER is required}"
  : "${LAMBDA_SSH_KEY_PATH:?LAMBDA_SSH_KEY_PATH is required}"
  [[ -f "$LAMBDA_SSH_KEY_PATH" ]] || { echo "LAMBDA_SSH_KEY_PATH must name a readable file" >&2; return 1; }
}

remote_dir() { printf '%s' "${INFERENCE_REMOTE_DIR:-~/ai-analytics-inference}"; }
ssh_target() { printf '%s@%s' "$LAMBDA_SSH_USER" "$LAMBDA_SSH_HOST"; }
ssh_cmd() { ssh -i "$LAMBDA_SSH_KEY_PATH" -o StrictHostKeyChecking=accept-new "$(ssh_target)" "$@"; }

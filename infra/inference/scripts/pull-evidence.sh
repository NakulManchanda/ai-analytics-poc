#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "${1:-}" == "--help" ]]; then echo "Usage: pull-evidence.sh RUN_ID"; exit 0; fi
load_local_env; require_connection
run_id="${1:?RUN_ID is required}"; mkdir -p "$INFERENCE_ROOT/../../../metrics/inference/$run_id"
rsync -az -e "ssh -i $LAMBDA_SSH_KEY_PATH" "$(ssh_target):$(remote_dir)/evidence/$run_id/" "$INFERENCE_ROOT/../../../metrics/inference/$run_id/"

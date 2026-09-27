#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: sync.sh [--plan]"; exit 0
fi
if [[ "${1:-}" == "--plan" ]]; then
  echo "source: infra/inference"
  echo "exclude: .env credentials metrics services/app services/mcp web .pem .key"
  exit 0
fi
load_local_env
require_connection
SOURCE="$INFERENCE_ROOT/"
TARGET="$(remote_dir)/"
ssh_cmd "mkdir -p $TARGET"
rsync -az --delete -e "ssh -i $LAMBDA_SSH_KEY_PATH -o StrictHostKeyChecking=accept-new" \
  --exclude '.env' --exclude 'credentials' --exclude 'metrics' --exclude 'services/app' \
  --exclude 'services/mcp' --exclude 'web' --exclude '*.pem' --exclude '*.key' \
  "$SOURCE" "$(ssh_target):$TARGET"

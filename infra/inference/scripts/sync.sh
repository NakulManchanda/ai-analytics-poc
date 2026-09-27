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

RDIR="$(remote_dir)"
# Restrict destructive rsync to safe, non-root directory targets
if [[ -z "$RDIR" || "$RDIR" == "/" || "$RDIR" == "~" || "$RDIR" == "/home" || "$RDIR" == "/root" ]]; then
  echo "ERROR: Refusing to sync to unsafe remote directory '$RDIR'" >&2
  exit 1
fi

SOURCE="$INFERENCE_ROOT/"
TARGET="$RDIR/"
ssh_cmd "mkdir -p '$TARGET'"
rsync -az --delete -e "ssh -i $LAMBDA_SSH_KEY_PATH -o StrictHostKeyChecking=accept-new" \
  --exclude '.env' --exclude 'credentials' --exclude 'metrics' --exclude 'services/app' \
  --exclude 'services/mcp' --exclude 'web' --exclude '*.pem' --exclude '*.key' \
  "$SOURCE" "$(ssh_target):\"$TARGET\""

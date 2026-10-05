#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: restart-test.sh [TARGET_DEPLOYMENT] [OUTPUT_DIR]"
  echo "Performs a deliberate rollout restart of a worker, records lifecycle provenance,"
  echo "and verifies post-restart recovery and smoke readiness."
  exit 0
fi

load_local_env
require_connection

DEPLOYMENT="${1:-inference-worker-b}"
OUTPUT_DIR="${2:-}"
: "${INFERENCE_NAMESPACE:=inference-lab}"
MODEL="${INFERENCE_MODEL:-Qwen/Qwen3-0.6B}"

echo "== Initiating deliberate restart test for $DEPLOYMENT =="

# 1. Capture old pod UID and name
OLD_POD_INFO=$(ssh_cmd "
  kubectl get pod -n '$INFERENCE_NAMESPACE' -l app='$DEPLOYMENT' \
    -o jsonpath='{.items[0].metadata.name} {.items[0].metadata.uid}' 2>/dev/null || echo 'unknown unknown'
")
OLD_POD_NAME=$(echo "$OLD_POD_INFO" | awk '{print $1}')
OLD_POD_UID=$(echo "$OLD_POD_INFO" | awk '{print $2}')
if [[ -z "$OLD_POD_NAME" || -z "$OLD_POD_UID" || "$OLD_POD_NAME" == "unknown" || "$OLD_POD_UID" == "unknown" ]]; then
  echo "ERROR: Failed to retrieve existing pod identity for $DEPLOYMENT in namespace $INFERENCE_NAMESPACE" >&2
  exit 1
fi
echo "  Old pod name: $OLD_POD_NAME"
echo "  Old pod UID:  $OLD_POD_UID"

# 2. Record rollout initiation time
INITIATION_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
T_START_EPOCH=$(date +%s)
echo "  Rollout restart initiated at: $INITIATION_TIME"

# 3. Trigger rollout restart on remote cluster
ssh_cmd "kubectl rollout restart deployment/'$DEPLOYMENT' -n '$INFERENCE_NAMESPACE'"

# 4. Wait for rollout status to finish
echo "  Waiting for new pod to reach Ready status..."
ssh_cmd "kubectl rollout status deployment/'$DEPLOYMENT' -n '$INFERENCE_NAMESPACE' --timeout=300s"
T_READY_EPOCH=$(date +%s)
READY_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# 5. Capture new pod details
NEW_POD_INFO=$(ssh_cmd "
  kubectl get pod -n '$INFERENCE_NAMESPACE' -l app='$DEPLOYMENT' \
    -o jsonpath='{.items[0].metadata.name} {.items[0].metadata.uid} {.items[0].status.containerStatuses[0].state.running.startedAt}' 2>/dev/null || echo 'unknown unknown unknown'
")
NEW_POD_NAME=$(echo "$NEW_POD_INFO" | awk '{print $1}')
NEW_POD_UID=$(echo "$NEW_POD_INFO" | awk '{print $2}')
NEW_STARTED_AT=$(echo "$NEW_POD_INFO" | awk '{print $3}')

if [[ -z "$NEW_POD_NAME" || -z "$NEW_POD_UID" || "$NEW_POD_NAME" == "unknown" || "$NEW_POD_UID" == "unknown" ]]; then
  echo "ERROR: Failed to retrieve replacement pod identity for $DEPLOYMENT after rollout restart" >&2
  exit 1
fi

if [[ "$OLD_POD_UID" == "$NEW_POD_UID" ]]; then
  echo "ERROR: Pod UID did not change after rollout restart ($OLD_POD_UID == $NEW_POD_UID)" >&2
  exit 1
fi

RECOVERY_DURATION=$((T_READY_EPOCH - T_START_EPOCH))
echo "  New pod name:         $NEW_POD_NAME"
echo "  New pod UID:          $NEW_POD_UID"
echo "  New container start:  $NEW_STARTED_AT"
echo "  Ready condition at:   $READY_TIME"
echo "  Recovery duration:    ${RECOVERY_DURATION}s"

# 6. Post-restart smoke probe
echo "  Running post-restart smoke check..."
SMOKE_RESULT="passed"
if ! ssh_cmd "
  WORKER_IP=\$(kubectl get svc -n '$INFERENCE_NAMESPACE' '$DEPLOYMENT' -o jsonpath='{.spec.clusterIP}')
  curl -s --fail --max-time 10 \"http://\$WORKER_IP:8000/v1/models\" >/dev/null || exit 1
"; then
  SMOKE_RESULT="failed"
fi

echo "  Post-restart smoke result: $SMOKE_RESULT"

# 7. Write restart_recovery_summary.json
SUMMARY_JSON=$(cat <<EOF
{
  "target_deployment": "$DEPLOYMENT",
  "old_pod_name": "$OLD_POD_NAME",
  "old_pod_uid": "$OLD_POD_UID",
  "restart_initiation_time": "$INITIATION_TIME",
  "new_pod_name": "$NEW_POD_NAME",
  "new_pod_uid": "$NEW_POD_UID",
  "new_container_started_at": "$NEW_STARTED_AT",
  "ready_time": "$READY_TIME",
  "recovery_duration_seconds": $RECOVERY_DURATION,
  "post_restart_smoke_result": "$SMOKE_RESULT"
}
EOF
)

if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
  echo "$SUMMARY_JSON" > "$OUTPUT_DIR/restart_recovery_summary.json"
  echo "Wrote restart recovery summary to $OUTPUT_DIR/restart_recovery_summary.json"
fi

REMOTE_BASE="$(remote_dir)"
# Also place on remote host so pull-evidence.sh rsyncs it
ssh_cmd "
  LATEST_DIR=\$(ls -td $REMOTE_BASE/evidence/* 2>/dev/null | head -n 1 || echo '')
  if [[ -n \"\$LATEST_DIR\" ]]; then
    cat <<'REMOTE_EOF' > \"\$LATEST_DIR/restart_recovery_summary.json\"
$SUMMARY_JSON
REMOTE_EOF
  fi
"

if [[ "$SMOKE_RESULT" != "passed" ]]; then
  echo "ERROR: Post-restart smoke check failed for $DEPLOYMENT" >&2
  exit 1
fi

echo "== Restart test completed successfully =="

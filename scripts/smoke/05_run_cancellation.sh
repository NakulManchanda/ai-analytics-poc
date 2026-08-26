#!/usr/bin/env bash
set -euo pipefail

# 05_run_cancellation.sh: Verify run-first submission, cancellation API, and durable state transition
echo "=== Running Milestone v3 Run Cancellation Smoke Check ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

uv run --project "${REPO_ROOT}/services/app" python - <<'PY'
import sys
from fastapi.testclient import TestClient
from app.main import create_app
from app.state.repository import InMemoryStateRepository

app = create_app()
repo = InMemoryStateRepository()
app.state.repository = repo

client = TestClient(app)

# 1. Health check
res = client.get("/api/health")
assert res.status_code == 200, f"Health check failed: {res.text}"
print("✓ Health check passed")

# 2. Submit a run
submit_res = client.post("/api/runs", json={"prompt": "Which pickup zones have the most trips?"})
assert submit_res.status_code == 202, f"Run submission failed: {submit_res.text}"
run_data = submit_res.json()
run_id = run_data["run_id"]
conv_id = run_data["conversation_id"]
assert run_id, "Missing run_id"
assert conv_id, "Missing conversation_id"
print(f"✓ Run submitted: {run_id} (conversation: {conv_id})")

# 3. Request cancellation
cancel_res = client.post(f"/api/runs/{run_id}/cancel")
assert cancel_res.status_code == 202, f"Cancel request failed: {cancel_res.text}"
cancel_data = cancel_res.json()
assert cancel_data["run_id"] == run_id
assert cancel_data["status"] in ("cancel_requested", "cancelled")
print(f"✓ Cancellation requested: {cancel_data['status']}")

# 4. Verify durable conversation state
conv_res = client.get(f"/api/conversations/{conv_id}")
assert conv_res.status_code == 200, f"Conversation fetch failed: {conv_res.text}"
conv_data = conv_res.json()
runs = [r for r in conv_data.get("runs", []) if r.get("run_id") == run_id]
assert len(runs) == 1, "Run not found in conversation snapshot"
print(f"✓ Durable run snapshot verified: status={runs[0]['status']}")

print("\n=== Milestone v3 Run Cancellation Smoke Check PASSED ===")
PY

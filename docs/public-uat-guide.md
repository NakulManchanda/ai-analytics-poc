# Public Cloud User Acceptance Testing (UAT) Guide

This guide describes how to verify the deployed AI Analytics application in AWS (`us-east-1`). It covers the deployed multi-service architecture, real-time voice input, streaming SSE telemetry, run cancellation, and durable state survival across ECS task replacements.

---

## 1. Demo Runtime Status (Cost Governance)

To keep cloud costs near $0 when not actively running tests, the live AWS demo backend is intentionally **parked** (`demo_enabled = false`).

- **While Parked**:
  - CloudFront CDN and the S3 static frontend UI remain reachable at `https://ai.sibkaro.com`.
  - Backend API requests (`/api/*`) return a fast, uncached JSON `503 Service Unavailable ("demo offline")` emitted by a CloudFront Function without waking backend tasks.
  - ECS Fargate tasks, the Application Load Balancer, and ElastiCache Redis are turned off.
- **Authoritative Durable State**:
  - The Amazon DynamoDB table (`ai-analytics-poc-demo-application-state`) remains active with its Global Secondary Index (`entity_type-started_at-index`), preserving all past conversations and audit logs.

### Resuming the Demo for Cloud UAT

When authorized to run live cloud verification:

```bash
# Option A: From GitHub Actions via Make (Recommended)
make tf-dispatch REF=main DEMO=true

# Option B: Locally via Terraform
cd infra/terraform
# Set demo_enabled = true in terraform.tfvars
make runtime-plan
make runtime-apply
```

Wait ~3–5 minutes for ECS tasks and ALB targets to report healthy, then proceed with the steps below.

---

## 2. Production URL Endpoints

Set the target origin in your terminal:

```bash
export APP_ORIGIN="https://ai.sibkaro.com"
```

| Layer | URL / Target | Expected Status |
| :--- | :--- | :--- |
| **Primary Domain** | `https://ai.sibkaro.com` | `200 OK` (React 18 SPA) |
| **Apex Domain** | `https://sibkaro.com` | `301/200` (Redirects to ai.sibkaro.com) |
| **API Health** | `https://ai.sibkaro.com/api/health` | `{"status":"ok","service":"ai-app"}` |
| **Voice WebSocket** | `wss://ai.sibkaro.com/ws/voice` | WebSocket upgrade (Amazon Transcribe streaming) |

---

## 3. Web UI Verification Walkthrough

Open **<https://ai.sibkaro.com>** in an authenticated or public browser window.

### A. Analytical Query Execution
1. In the prompt box, enter:
   ```text
   Which pickup zones had the highest trip volume?
   ```
2. Click **Run analysis**.
3. **Verify:**
   - Real-time SSE connection establishes to `/api/runs/{run_id}/events`.
   - The **Run Timeline Inspector** shows event progression: `run.received`, `llm.proposal`, `tool.requested`, `tool.completed`, `context.reduced`, and `run.completed`.
   - The assistant answers citing exact DuckDB numbers queried from 2.96M NYC taxi records via FastMCP over AWS Service Connect.

### B. Real-Time Voice Input (Speech-to-Text)
1. Click the **Voice** button next to the input field.
2. Grant microphone access in the browser.
3. Speak an analytics question clearly into your microphone.
4. Verify the animated waveform visualizer pulses with audio amplitude.
5. Click **Done Speaking**.
6. The continuous audio stream is delivered via secure WebSocket (`wss://ai.sibkaro.com/ws/voice`) to Amazon Transcribe Streaming, converted to text, and populated into the prompt box for immediate query execution.

### C. In-Flight Cancellation
1. Submit an expansive query (e.g., *"Break down payment types, trip distance, and tips across all boroughs"*).
2. Click the **Stop** button while the model is executing.
3. Verify the run aborts immediately using the sub-millisecond Redis coordination flag, and partial assistant output is preserved marked `[interrupted]`.

---

## 4. API & Two-Turn Conversation Verification (CLI)

Verify the cloud API contracts without using the browser:

```bash
# 1. Check Service & Tool Health
curl -fsS "${APP_ORIGIN}/api/status" | python3 -m json.tool

# 2. Turn 1: Initial Question (No conversation_id sent)
FIRST=$(curl -fsS -X POST "${APP_ORIGIN}/api/ask" \
  -H 'content-type: application/json' \
  -d '{"prompt":"Which pickup zones have the most trips?"}')

CONVERSATION_ID=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["conversation_id"])')
RUN_1=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "Conversation ID: $CONVERSATION_ID | Run 1: $RUN_1"

# 3. Turn 2: Follow-up using backend-returned conversation ID
SECOND=$(curl -fsS -X POST "${APP_ORIGIN}/api/ask" \
  -H 'content-type: application/json' \
  -d "{\"conversation_id\":\"${CONVERSATION_ID}\",\"prompt\":\"Show me the top three.\"}")

RUN_2=$(printf '%s' "$SECOND" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "Run 2: $RUN_2"

# 4. Fetch Durable Conversation & Replay Events
curl -fsS "${APP_ORIGIN}/api/conversations/${CONVERSATION_ID}" | python3 -m json.tool
curl -N "${APP_ORIGIN}/api/runs/${RUN_2}/events"
```

### Acceptance Criteria
- Both responses return the same `conversation_id` and unique `run_id`s.
- `GET /api/conversations/{id}` returns four chronological messages (`user`, `assistant`, `user`, `assistant`) persisted in Amazon DynamoDB.
- `GET /api/runs/{id}/events` replays ordered SSE telemetry with truthful token metrics.

---

## 5. Durable Task-Replacement Checkpoint

To prove state durability is independent of container lifecycles:

1. Record `${CONVERSATION_ID}` from a completed cloud session.
2. In the AWS Console or via AWS CLI, force a replacement of the `ai-app` ECS Fargate task:
   ```bash
   aws ecs update-service \
     --cluster ai-analytics-poc-demo-cluster \
     --service ai-analytics-poc-demo-ai-app \
     --force-new-deployment \
     --region us-east-1
   ```
3. Wait for the new task to become healthy.
4. Reload the conversation from a completely new browser tab or run:
   ```bash
   curl -fsS "${APP_ORIGIN}/api/conversations/${CONVERSATION_ID}" | python3 -m json.tool
   ```
5. Confirm all messages, runs, and tool execution steps restore completely from Amazon DynamoDB.

---

## 6. Parking the Demo Post-Testing

When testing is complete, park the runtime resources to return to a zero-spend state:

```bash
# Option A: From GitHub Actions (Recommended)
make tf-dispatch REF=main DEMO=false

# Option B: Locally via Terraform
cd infra/terraform
# Set demo_enabled = false in terraform.tfvars
make runtime-plan
make runtime-apply
```

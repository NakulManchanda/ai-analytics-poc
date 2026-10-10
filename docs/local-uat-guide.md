# Local User Acceptance Testing (UAT) Guide

This guide provides end-to-end instructions for testing the AI Analytics POC application locally. It covers real AWS orchestration (Bedrock, Transcribe, Polly, DynamoDB), the owned vLLM inference serve path, deterministic local mocks, real-time voice input/output, streaming SSE telemetry, and run cancellation.

---

## 1. Local vs. Remote Cloud: Why Local Docker + Remote AWS is Most Cost-Efficient

For daily testing, development, and user acceptance evaluations, running the **Local Docker Stack with Remote AWS Services (`make local-aws-compose`)** is the **most cost-efficient architecture** by a wide margin:

| Architecture | Compute Cost (Fargate/ALB/Redis) | Model & Voice Cost (Bedrock/Transcribe) | Total Monthly Spend |
| :--- | :--- | :--- | :--- |
| **Full Remote Cloud (AWS Fargate + ALB + ElastiCache)** | **~$90–$120/month** (Fixed hourly baseline even when 100% idle) | Pay-per-request | High idle overhead |
| **Local Docker + Remote AWS (`make local-aws-compose`)** | **$0.00** (Runs on host machine; no ALB, no Fargate, no NAT Gateway) | **~$0.001 per test run** (Strictly on-demand micro-cents) | **Under $1.00/month** |

### Why This Option Is Recommended:
1. **Zero Idle Cloud Spend**: All server compute (FastAPI orchestrator, FastMCP DuckDB server, Redis event broker, React 18 web UI) runs locally on Docker for free.
2. **Keep Remote Demo Parked**: The remote AWS demo environment remains parked (`demo_enabled = false`), eliminating CloudWatch, ALB, and Fargate standing costs.
3. **Pure Pay-Per-Request Pricing**:
   - Amazon Bedrock (Nova Micro): **$0.000035** / 1k input tokens, **$0.00014** / 1k output tokens (~$0.0001 per query).
   - Amazon Transcribe: **$0.0004** per second of audio.
   - Amazon DynamoDB: Free tier / on-demand pay-per-request.
4. **Governed Safety Cap**: All requests participate in the DynamoDB monthly allowance cap (`GLOBAL_BEDROCK_MONTHLY_LIMIT_USD=5.00`), mathematically preventing runaway costs.

---

## 2. Runtime Modes & Configured Defaults

### Available Modes

| Mode | Command | LLM Provider | Voice & Tools | State Storage | Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Real AWS Stack** | `make local-aws-compose` | AWS Bedrock (`amazon.nova-micro-v1:0`) | Transcribe STT + Polly TTS + FastMCP DuckDB | DynamoDB + Redis | **Recommended:** Real cloud AI with zero compute overhead |
| **Inference Serve Stack** | `make local-serve-compose` | Owned vLLM Gateway (`:18080/serve` on A100) | Transcribe STT + FastMCP DuckDB | DynamoDB + Redis | Testing Track B cluster gateway, queueing, and vLLM workers |
| **Deterministic Fake Stack** | `docker compose up --build -d` | Mock LLM (`fake`) | Mock STT + FastMCP DuckDB | In-Memory / Local Redis | Offline unit testing, CI checks, and rapid frontend iteration |

### Configured Defaults (`make local-aws-compose`)

When you run `make local-aws-compose`, the following defaults are automatically applied:

```bash
DYNAMODB_TABLE_NAME=ai-analytics-poc-demo-application-state # Shared DynamoDB state table
AWS_PROFILE=default                                         # Host AWS CLI credentials profile
AWS_REGION=us-east-1                                        # Primary AWS service region
WEB_PORT=3000                                               # Local web frontend port (http://localhost:3000)
VOICE_ENABLED=false                                         # Spoken answer audio (TTS) MUTED by default
STT_PROVIDER=transcribe                                     # Speech-to-text question input ACTIVE via Transcribe
LLM_PROVIDER=bedrock                                        # LLM routed to AWS Bedrock
LLM_MODEL_ID=amazon.nova-micro-v1:0                         # Cost-efficient frontier reasoning model
GLOBAL_BEDROCK_MONTHLY_LIMIT_USD=5.00                       # Monthly safety budget ceiling
LOCAL_UID=$(id -u)                                          # Host UID for container permission matching
AGENT_STRATEGY=manual                                       # Default: Bespoke iterative ReAct loop with context reduction
```

### Switching to CrewAI for Evaluation

To benchmark and evaluate our bespoke ReAct orchestration loop against an external multi-agent framework:

```bash
# Evaluate with CrewAI multi-agent strategy (Researcher + Writer agents):
AGENT_STRATEGY=crewai make local-aws-compose

# Switch back to bespoke ReAct loop:
AGENT_STRATEGY=manual make local-aws-compose
```

- **`manual` (Default)**: Our custom, deterministic ReAct loop (`think` → `tool call` → `observe` → `answer`). Strictly governed by execution budgets (30s wall-clock, 5 loop iterations, 3 tool calls), context compression via context reducer, and fast cancellation flags.
- **`crewai`**: Autonomous multi-agent coordination where a `Researcher` agent plans queries and executes DuckDB tools over FastMCP, while a `Writer` agent synthesizes analytical observations into the final answer.

---

## 3. Starting the Stack & Docker Verification

### Launch Command

```bash
# Starts the stack with all defaults (Bedrock active, Transcribe voice input active, answer speech muted, manual ReAct loop):
make local-aws-compose
```

*(Optional: If you explicitly want the application to speak answers aloud via Amazon Polly, run `VOICE_ENABLED=true make local-aws-compose`.)*

### Step-by-Step Docker Verification Commands

Always verify the stack after running Compose:

#### 1. Check Container Health & Status
```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml ps
```
Confirm that all 5 services are running and healthy:
- `ai_app_poc-redis-1` (healthy)
- `ai_app_poc-mcp-1` (healthy)
- `ai_app_poc-app-1` (healthy)
- `ai_app_poc-worker-1` (running)
- `ai_app_poc-web-1` (running, listening on `0.0.0.0:3000->8080/tcp`)

#### 2. Verify API & FastMCP Discovery Endpoint
```bash
curl -s http://localhost:3000/api/status | python3 -m json.tool
```
Expected output:
```json
{
  "app": {
    "status": "ok",
    "service": "ai-app"
  },
  "mcp": {
    "status": "ok",
    "tools": 7,
    "resources": 1
  }
}
```

#### 3. Inspect Running Container Environment
```bash
docker exec ai_app_poc-app-1 env | grep -E "VOICE_ENABLED|STT_PROVIDER|LLM_PROVIDER|DYNAMODB_TABLE_NAME"
```
Expected output:
```text
VOICE_ENABLED=false
STT_PROVIDER=transcribe
LLM_PROVIDER=bedrock
DYNAMODB_TABLE_NAME=ai-analytics-poc-demo-application-state
```

#### 4. Tail Logs for Active Invocations
```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml logs -f app mcp
```

---

## 4. Web UI Verification Walkthrough

Open **<http://localhost:3000>** in your browser.

### A. Ask an Analytics Question (Text Input)
1. Type a question into the prompt input box, for example:
   ```text
   What was the total trip count and average fare amount?
   ```
2. Click **Run analysis**.
3. **Observe the Execution Flow:**
   - The UI connects to the Server-Sent Events stream (`/api/runs/{run_id}/events`).
   - The **Run Timeline Inspector** tracks live lifecycle steps: `run.received`, `context.loading`, `llm.started`, `tool.requested`, `tool.started`, `tool.completed`, and `run.completed`.
   - The model calls FastMCP DuckDB tools (`average_trip_metrics` / `read_schema`) against the 2.96M NYC taxi records.
   - The assistant renders structured totals (e.g., 2,915,046 total rides across boroughs) with exact citations.

### B. Quick Preset Questions (Pills)
Click any categorized shortcut pill below the input box to verify specialized analytical queries:
- **Dataset**: `Dataset Profile`, `Code Dictionary`, `List Boroughs`
- **Time & Location**: `Top Pickup Zones`, `Peak Travel Hours`, `Weekday vs Weekend`, `Airport vs Non-Airport`, `Longest Trips by Zone`
- **Fares & Payments**: `Borough Fare Comparison`, `Payment & Tip Breakdown`, `Tip Rate by Borough`, `Fare Buckets`, `Fare by Distance Bucket`

### C. Voice Question Submission (STT)
1. Click the **Voice** button next to the input field.
2. Grant microphone permissions in the browser.
3. Speak an analytics question clearly (e.g., *"Show me the top five pickup locations"*).
4. Watch the animated real-time audio waveform visualizer.
5. Click **Done Speaking** (or pause). The audio is delivered via WebSocket to Amazon Transcribe, converted into text, and inserted into the prompt input box for immediate analysis.

### D. In-Flight Run Cancellation (Fast Abort)
1. Submit an expansive query (e.g., *"Compare tip rates across all boroughs by hour of day"*).
2. While the model is thinking or executing DuckDB queries, click the red **Stop / Cancel** button.
3. Observe immediate cooperative termination:
   - The Redis fast-flag (`run:cancel:{run_id}`) halts the orchestration loop sub-millisecond.
   - Any partial generated response is preserved in the timeline marked with `[interrupted]`.
   - The run transitions cleanly to an interrupted state without hanging.

---

## 5. API & Durability Verification (Command Line)

Exercise the full two-turn durable conversation contract directly via `curl`:

```bash
# Turn 1: Submit first question (no conversation_id sent)
FIRST=$(curl -sS -X POST http://localhost:3000/api/ask \
  -H 'content-type: application/json' \
  -d '{"prompt":"Which pickup zones have the most trips?"}')

CONVERSATION_ID=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["conversation_id"])')
RUN_1=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "Conversation ID: $CONVERSATION_ID | Run 1: $RUN_1"

# Turn 2: Follow-up question referencing the backend-returned conversation ID
SECOND=$(curl -sS -X POST http://localhost:3000/api/ask \
  -H 'content-type: application/json' \
  -d "{\"conversation_id\":\"${CONVERSATION_ID}\",\"prompt\":\"Show me the top three with exact trip counts.\"}")

RUN_2=$(printf '%s' "$SECOND" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "Run 2: $RUN_2"

# Inspect durable conversation state and replayed SSE events
curl -sS "http://localhost:3000/api/conversations/${CONVERSATION_ID}" | python3 -m json.tool
curl -N "http://localhost:3000/api/runs/${RUN_2}/events"
```

### Acceptance Checks
- `conversation_id` is identical across both turns; `run_id` is unique per turn.
- `GET /api/conversations/{id}` returns four chronological messages (`user`, `assistant`, `user`, `assistant`).
- `GET /api/runs/{id}/events` streams valid `text/event-stream` ordered events starting with `run.received`.
- Working context is compressed by the context reducer between turns, avoiding prompt bloat.

### Fast Local Unit Smoke (No Docker Required)

To verify the API contract and in-memory event reconstruction in seconds without running Docker:

```bash
uv run --project services/app pytest services/app/tests/test_v11_integration_smoke.py -q
```

---

## 6. Starting the Owned Inference Serve Stack (Track B)

When testing the inference cluster gateway and vLLM workers (instead of Bedrock):

```bash
# Assumes the local SSH tunnel to the A100 cluster is active (port 18080):
make local-serve-compose
```

In this mode:
- All LLM completions route through `http://host.docker.internal:18080/serve`.
- Model requests go through the 5 gateway stages: Guard (`guard.py`), Tenant Quotas (`tenants.py`), Admission Control (`admission.py`), Priority Queue (`queueing.py`), and Prefix Router (`placement.py`).
- Sliced vLLM workers execute continuous batching with prefix caching.

---

## 7. Teardown

To stop the running containers and clean up resources:

```bash
# If running the Real AWS Stack:
docker compose -f docker-compose.yml -f docker-compose.aws.yml down

# If running the Inference Serve Stack:
docker compose -f docker-compose.yml -f docker-compose.serve.yml down

# If running the Default Mock Stack:
docker compose down
```

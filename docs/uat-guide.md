# User Acceptance Testing (UAT) Manual Runbook

This document contains step-by-step instructions to manually verify the complete AI Analytics POC system end-to-end.

---

## Pre-Requisites & Environment Startup

From the repository root (`/Users/nakulmanchanda/dev/ai_app_poc`):

```bash
# 1. Activate the Python virtual environment
source .venv/bin/activate

# 2. Start the full 5-service local stack (React UI, FastAPI app, FastMCP server, Redis, Worker)
docker compose up --build -d
```

---

## UAT Test Cases

### Test Case 1: System Health & FastMCP Capability Discovery
*Objective*: Verify that the Gateway and MCP server are discoverable without making paid LLM calls.

1. **Open your browser** to: [http://localhost:3000](http://localhost:3000)
2. **Observe Header Badges**:
   - [ ] Backend status displays: `Backend ready` (green badge).
   - [ ] FastMCP status displays: `MCP discovered · 2 tools · 1 resources`.
3. **Command-line verification**:
   ```bash
   curl -s http://localhost:3000/api/status | python3 -m json.tool
   ```
   - [ ] Expected JSON output includes `"status": "ok"` for `app` and `mcp`.

---

### Test Case 2: Interactive AI Analytics Query & Telemetry Inspectors
*Objective*: Verify the bounded multi-step agent loop, tool execution over MCP, and real-time SSE progress & context reduction.

1. **In the Web UI ([http://localhost:3000](http://localhost:3000))**:
   - In the query prompt input, enter or click a sample question (e.g. `Which pickup zones have the most trips?`).
   - Click **Run analysis**.
2. **Verify the "Run Timeline (SSE)" Tab**:
   - [ ] Switch to **Run Timeline (SSE)** tab (or observe live during execution).
   - [ ] Status badge displays **`● COMPLETED`** (or **`● STREAMING`** while active).
   - [ ] Event `run.received` appears showing the run initialization.
   - [ ] Event `tool.requested` appears showing tool proposal of `query_taxi_data` (analysis & limit).
   - [ ] Event `tool.completed` appears showing DuckDB MCP execution with row count and query ID.
   - [ ] Event `run.completed` appears showing total token usage and execution latency.
   - [ ] Final synthesized answer appears in the main answer card on the left.
3. **Verify the "Working Context Panel" Tab (Context Reducer Inspector)**:
   - [ ] Switch to **Working Context Panel** tab.
   - [ ] **Top Metric Cards**: Displays `Stored Messages (DynamoDB)`, `Messages in LLM Context`, and `Schema Context Size` (184 B).
   - [ ] **Core AI Thesis Badge**: Explains bounded prompt window vs authoritative durable history.
   - [ ] **Recent Observations & Artifacts**: Shows DuckDB query ID, preview rows, and `artifact://` URI references.
   - [ ] **Run Execution Budgets**: Shows progress bars for Current Iteration (e.g. 1/6), Remaining Tool Calls (e.g. 7/8), Remaining Tokens, and Estimated Cost Budget.

---

### Test Case 3: Asynchronous Job Submission & Background Worker
*Objective*: Verify async job dispatch via Redis queue, background worker processing, and durable state storage.

1. **Submit an asynchronous report job via cURL**:
   ```bash
   curl -X POST http://localhost:3000/api/jobs \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Generate full monthly trip volume summary for January 2024"}' \
     | python3 -m json.tool
   ```
2. **Verify immediate acceptance**:
   - [ ] Response returns HTTP 200 with `"status": "submitted"`, `"job_id": "job-..."`, and a `"job_url"`.
3. **Poll the job status until completed**:
   ```bash
   # Replace <JOB_ID> with the job_id returned above
   curl -s http://localhost:3000/api/jobs/<JOB_ID> | python3 -m json.tool
   ```
   - [ ] Within ~3–5 seconds, `"status"` transitions from `"in_progress"` to `"completed"`.
   - [ ] `"result"` contains the generated analytical report.

---

### Test Case 4: Historical Run Event Replay
*Objective*: Verify that event streams are reconstructible and can be replayed for auditability.

1. **Query run events with replay**:
   ```bash
   # Run a test ask query to get a run_id
   RUN_ID=$(curl -s -X POST http://localhost:3000/api/ask \
     -H "Content-Type: application/json" \
     -d '{"prompt": "What is the total trip count?", "conversation_id": "conv_replay_test"}' | python3 -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')
   
   echo "Replaying events for Run: ${RUN_ID}"
   
   # Replay the SSE stream from the beginning
   curl -N "http://localhost:3000/api/runs/${RUN_ID}/events"
   ```
   - [ ] Stream outputs standard SSE format `event: run.received`, `event: tool.requested`, etc. in order from step 1 to completion.

---

### Test Case 5: Infrastructure Static Validation (AWS Zero-NAT & CloudFront)
*Objective*: Verify that the Terraform code for ALB, ECS Fargate, S3 OAC, and CloudFront is syntactically sound and passes validation.

```bash
make -C infra/terraform fmt-check
make -C infra/terraform validate
```
- [ ] Output prints `Success! The configuration is valid.` with 0 errors.

---

## Teardown

When you have finished testing:

```bash
docker compose down
```

# Public Cloud User Acceptance Testing (UAT) Guide

This guide is designed for stakeholders, product managers, and external evaluators testing the live **AI Analytics POC** deployed on AWS via Amazon CloudFront.

No local setup, Docker, or Python installation is required to test the live web interface.

---

## Public Cloud URL

Access the production deployment at your live custom domain:
👉 **`https://sibkaro.com`** or **`https://ai.sibkaro.com`**

*(Direct CloudFront URL fallback: `terraform -chdir=infra/terraform output -raw cloudfront_url`)*

---

## Acceptance Test Scenarios

### Scenario 1: Health & Capability Discovery (Zero-LLM Verification)
*Objective*: Confirm that the edge CDN, Application Load Balancer, and FastMCP analytics gateway are healthy and discoverable without triggering billable model calls.

1. Open **`https://sibkaro.com`** (or **`https://ai.sibkaro.com`**) in any modern web browser.
2. Verify the top status board badges:
   - [ ] **Backend Status**: Displays `Backend ready` (green indicator).
   - [ ] **FastMCP Status**: Displays `MCP discovered · 2 tools · 1 resources`.
3. *(Optional)* API verification via terminal:
   ```bash
   curl -s https://ai.sibkaro.com/api/status | python3 -m json.tool
   ```
   - [ ] Returns HTTP 200 with `"status": "ok"` for both `app` and `mcp`.

---

### Scenario 2: Interactive AI Analytics Query & Telemetry Streaming
*Objective*: Execute an interactive analytics query against the 2.96M NYC taxi trip dataset using the governed FastMCP DuckDB engine and observe real-time Server-Sent Events (SSE) and context reduction.

1. **Submit an Analytical Query**:
   - In the prompt input area, click one of the **Sample Question** chips (e.g., `📍 Top Pickup Zones` or `⏰ Peak Travel Hours`), or enter your own prompt:
     ```text
     Which pickup zones have the most trips?
     ```
   - Click **Run analysis**.
2. **Verify the "Run Timeline (SSE)" Inspector**:
   - [ ] Badge transitions to **`● STREAMING`** and completes as **`● COMPLETED`**.
   - [ ] Step **`run.received`**: Displays the initialized execution run.
   - [ ] Step **`tool.requested`**: Displays the model's proposal of `query_taxi_data` with parameters.
   - [ ] Step **`tool.completed`**: Displays DuckDB query execution with exact row counts and duration.
   - [ ] Step **`run.completed`**: Displays token telemetry and overall latency.
   - [ ] The synthesized answer appears in the main answer panel.
3. **Verify the "Working Context Panel" Inspector**:
   - [ ] Switch to the **Working Context Panel** tab.
   - [ ] Inspect **Schema Context Size** (184 bytes), **Messages in LLM Context**, and **Stored Messages (DynamoDB)**.
   - [ ] Observe the **Run Execution Budgets** (visual meters for iterations, tool calls, tokens, and USD cost boundaries).

---

### Scenario 3: Asynchronous Report Job Processing
*Objective*: Dispatch an asynchronous report job via the `/api/jobs` endpoint and retrieve the generated daily zone analytics report.

1. **Submit the asynchronous job**:
   ```bash
   curl -X POST https://ai.sibkaro.com/api/jobs \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Generate full monthly trip volume summary for January 2024"}' \
     | python3 -m json.tool
   ```
   - [ ] Immediate response returns HTTP 200 with `"status": "PENDING"` (or `"submitted"`) and a unique `"job_id"`.
2. **Poll the completed report**:
   ```bash
   # Replace <JOB_ID> with the job_id returned above
   curl -s https://ai.sibkaro.com/api/jobs/<JOB_ID> | python3 -m json.tool
   ```
   - [ ] Status transitions to `"COMPLETED"` within seconds.
   - [ ] `"result"` contains the aggregated daily zone summary across 2,964,624 taxi trips.

---

### Scenario 4: Historical Event Stream Audit & Replay
*Objective*: Verify that complete run execution traces are durably recorded and can be replayed via SSE for auditability.

```bash
# 1. Ask a question and extract the run_id
RUN_ID=$(curl -s -X POST https://ai.sibkaro.com/api/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What are the peak hours for taxi rides?", "conversation_id": "conv_public_audit"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["run_id"])')

echo "Auditing Run: ${RUN_ID}"

# 2. Replay the complete execution stream
curl -N "https://ai.sibkaro.com/api/runs/${RUN_ID}/events"
```
- [ ] Outputs ordered SSE stream events (`run.received` $\rightarrow$ `tool.requested` $\rightarrow$ `tool.completed` $\rightarrow$ `run.completed`).

---

## Security & Architecture Highlights

- **Edge Security**: HTTPS enforced via AWS CloudFront CDN with TLS 1.3.
- **SigV4 Origin Access Control (OAC)**: The S3 frontend bucket is private with zero public bucket policies.
- **Least-Privilege Task Roles**: Backend services run in private subnets with IAM roles restricted to necessary DynamoDB, S3, and Bedrock actions.
- **Budget Hard Limits**: Token and loop iteration ceilings prevent runaway inference loops or unexpected costs.

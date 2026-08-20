# Work History Entry: 0025

**Goal**: Deliver local and public User Acceptance Testing (UAT) runbooks, fix SSE streaming race conditions and state persistence for synchronous ask runs, and add interactive UI sample questions and tooltips.

**Starting Point**: All milestones 0 through 16 were completed and merged to `main`. Manual UAT revealed a premature 404 in the Server-Sent Events stream due to missing lifecycle event publishing in `/api/ask` and unhandled conversation foreign-key constraints in `InMemoryStateRepository`.

---

## Decisions & Changes

1. **SSE Lifecycle Event Publishing in `/api/ask`**:
   - Updated `services/app/app/routers/ask.py` to publish structured execution events (`run.received`, `tool.requested`, `tool.completed`, `run.completed`) to Redis Streams.
   - Initialized and persisted `Conversation`, `Run`, and `RunStep` records properly with valid schema fields.
   - Updated `/api/runs/{run_id}/events` in `services/app/app/routers/events.py` to check Redis `run:{run_id}` and gracefully stream events.
2. **Web UI Tooltips & Sample Questions**:
   - Added explanatory tooltips to the **Run Timeline (SSE)** and **Working Context Panel** tabs in `web/src/App.tsx`.
   - Added clickable **Sample Question Chips** (`📍 Top Pickup Zones`, `⏰ Peak Travel Hours`, `🗺️ Borough Fare Comparison`, `💳 Payment & Tip Breakdown`) for quick testing.
   - Synchronized `activeRunId` connection timing to use the authoritative `run_id` returned by the backend.
3. **Custom Domain & Route 53 Aliases (`ai.sibkaro.com` & `sibkaro.com`)**:
   - Provisioned ACM SSL certificate across `sibkaro.com`, `ai.sibkaro.com`, and `*.sibkaro.com` with Route 53 DNS validation.
   - Wired Route 53 `A` and `AAAA` alias records directly to the CloudFront distribution with dynamic Terraform variables (`custom_domain_name`, `custom_subdomain_name`, `enable_custom_domain`).
4. **ECS Service Connect & Query Execution Deadline**:
   - Built and pushed container images for `ai-app` and `analytics-mcp` to Amazon ECR.
   - Updated `services/app/app/routers/status.py` to support `MCP_URL` / `MCP_SERVER_URL` and catch connection exceptions gracefully.
   - Increased DuckDB query timeout from 10.0s to 30.0s in `analytics.py` and `server.py` to support multi-million row aggregation in Fargate vCPU limits.
5. **Local & Public UAT Guides**:
   - Updated `docs/local-uat-guide.md` with port 3000 ingress endpoints and updated UI checklist.
   - Created `docs/public-uat-guide.md` for external stakeholders evaluating the live AWS CloudFront deployment.

---

## Verification

1. **Automated Tests**:
   - Backend unit and router test suite: `pytest services/app/tests` $\rightarrow$ **76/76 passed**.
   - MCP and dataset spike tests: `pytest services/mcp/tests services/dataset_spike/tests` $\rightarrow$ **14/14 passed**.
   - Frontend Vitest suite and production build: `npm --prefix web test && npm --prefix web run build` $\rightarrow$ **10/10 passed**.
2. **Cloud End-to-End Verification**:
   - Verified live HTTPS capability discovery on `https://ai.sibkaro.com/api/status` $\rightarrow$ returns `app: ok`, `mcp: ok` (2 tools, 1 resource).
   - Verified live governed analytics query against 2024 NYC Yellow Taxi dataset via `https://ai.sibkaro.com/api/ask` $\rightarrow$ returns synthesized Bedrock answer and full 5-step event trace in 4.7s.
   - Verified custom domain alias resolution for both `ai.sibkaro.com` and `sibkaro.com` over CloudFront HTTP/2.

---

## PR and Merge State

- Branch: `feat/uat-guides-and-telemetry-fix`
- Pull Request: [PR #43](https://github.com/NakulManchanda/ai-analytics-poc/pull/43) (Merged to `main` via commit `05a8dc3`)
- Tracking Issue: Roadmap Issue #32 (Closed)
- Public UAT Sign-off: Completed and verified live by operator.

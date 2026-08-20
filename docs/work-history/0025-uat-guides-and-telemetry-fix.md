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
3. **Local & Public UAT Guides**:
   - Updated `docs/uat-guide.md` with port 3000 ingress endpoints and updated UI checklist.
   - Created `docs/public-uat-guide.md` for external stakeholders evaluating the live AWS CloudFront deployment.

---

## Verification

1. **Automated Tests**:
   - Backend unit and router test suite: `pytest services/app/tests` $\rightarrow$ **76/76 passed**.
   - Frontend Vitest suite and production build: `npm --prefix web test && npm --prefix web run build` $\rightarrow$ **10/10 passed**.
2. **Manual & Docker Integration Smoke Verification**:
   - Verified synchronous ask and live SSE streaming via `curl -N http://localhost:3000/api/runs/${RUN_ID}/events` $\rightarrow$ streamed all 4 events with 0 errors.
   - Verified asynchronous job submission and polling on `http://localhost:3000/api/jobs`.
   - Verified Terraform formatting and validation: `make -C infra/terraform fmt-check && make -C infra/terraform validate` $\rightarrow$ **0 errors**.

---

## PR and Merge State

- Branch: `feat/uat-guides-and-telemetry-fix`
- Tracking Issue: Roadmap Issue #32

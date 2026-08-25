# Work History Entry: 0029

**Goal**: Make the browser render v1.1 conversation, context, and run telemetry from the merged backend contracts without synthesizing runtime state.

**Starting Point**: `f55d331` had merged durable conversation reload, truthful telemetry/context reconstruction, and stable Timeline Inspector SSE lifecycle. The React application still generated a conversation ID, constructed fallback Working Context values, and showed only aggregate synchronous latency.

---

## Decisions & Changes

1. The first `POST /api/ask` contains only the prompt. The app stores and reuses the backend-issued `conversation_id`, shows the backend conversation/run IDs immediately, and clears that pointer when starting a new conversation.
2. On reload, the app uses the stored backend ID solely to request `GET /api/conversations/{conversation_id}` and rebuilds ordered messages, associated run metadata, and the active run from the durable snapshot.
3. Removed every App-level Working Context fallback, including synthetic schema, preview rows, artifacts, budgets, message counts, and generated IDs. The Context Inspector now remains empty until the live or reconstructed SSE `context.reduced` payload arrives.
4. Terminal SSE events feed backend-owned phase latency, token totals, estimated cost, and explicit blocking-mode TTFT status into the answer telemetry display. TimelineInspector keeps callback references in refs, retaining the run-ID-only SSE lifecycle merged in #47.
5. Review round 1 refreshes the durable snapshot after each successful ask, guards hydration with a monotonic request version, and clears run-scoped context/telemetry before a run switch. The snapshot attaches a run to the immediately following assistant message only when the ordered user message named by `Run.message_id` makes that relationship deterministic.
6. TimelineInspector now forwards the source run ID with context and terminal telemetry callbacks. This sequential modification is safe because #47 is merged: it preserves that PR's run-ID-only effect dependency and ref-backed callback lifecycle, while supplying the run identity needed for App to reject stale run facts.

## Verification

1. Red tests: `cd web && npm test -- App.test.tsx` initially failed for absent first-turn backend IDs and reload hydration. `cd web && npm test -- TimelineInspector.test.tsx` then failed because no terminal telemetry was forwarded.
2. Focused frontend tests: `cd web && npm test -- App.test.tsx TimelineInspector.test.tsx` passed with 15 tests.
3. Full frontend test suite: `cd web && npm test` passed.
4. Production build: `cd web && npm run build` passed (`tsc --noEmit && vite build`).
5. Diff checks: `git diff --check` passed; changed frontend source was searched for the removed synthesized Working Context constants before handoff.
6. Review round 1 focused regression tests: `cd web && npm test -- App.test.tsx TimelineInspector.test.tsx` passed with 17 tests; production build passed.

## Pull Request and Merge State

- Branch: `codex/v11-truthful-ui`
- Tracking issue: #48
- Pull request: [PR #53](https://github.com/NakulManchanda/ai-analytics-poc/pull/53) (draft).
- Merge state: not merged. Deployment, backend changes, infrastructure changes, and v2 streaming/cancellation remain out of scope.

## Lessons

A browser can retain an opaque backend identity pointer for reload convenience without becoming the authority for conversation state. UI telemetry should likewise wait for the terminal event contract rather than inferring phase timings or TTFT from a blocking response.

# 0031 — v2 streaming text and live answer events

## Goal

Advance issue #61 from the blocking v1.1 answer lifecycle to genuine provider-streamed text over
live run-first SSE. Persist the complete assistant answer after successful streaming and expose
truthful TTFT without adding voice, cancellation, or durable Redis state.

## Starting point

- Branch: `codex/v2-streaming-text`
- Worktree: `.worktrees/v2-streaming-text`
- Base: `origin/main` at `2febb216` (`v1.1: verify durable state integration boundary (#56)`)
- Release baseline: annotated tag `v1.1-foundation-truthful-state`
- `POST /api/ask` synchronously runs the complete orchestration loop and returns only the final
  answer.
- `GET /api/runs/{run_id}/events` exposes SSE, but live delivery depends on Redis and durable
  fallback primarily reconstructs terminal runs.
- The final Bedrock answer call uses the blocking Converse response and produces one complete
  `LLMResult`; no real provider delta contract or measurable streaming TTFT exists.

## Decisions

- Use the run-first lifecycle from `ai_analytics_poc_realtime_multimodal_plan.md`: persist the run,
  return `202 Accepted`, and allow SSE to connect before orchestration completes.
- Emit `answer.delta` only from genuine provider-streamed chunks. Never simulate streaming by
  splitting a completed string.
- Keep the application responsible for the LLM stream, budgets, event order, and durable final
  message. DynamoDB remains authoritative; Redis issue #57 supplies transient delivery only.
- Keep cancellation, voice, STT, TTS, Pipecat, and multimodal inputs outside this issue.

## TDD and verification

### Slice 1 — run-first endpoint GREEN (handoff commit 306e8df)
Already green on pickup.

Command: `uv run --project services/app pytest services/app/tests/test_streaming_runs.py -q`
Result: 1 passed

### Slice 2 — prepare_run durable state
RED: `AttributeError: 'OrchestrationLoop' object has no attribute 'prepare_run'`
Command: `uv run --project services/app pytest services/app/tests/test_orchestration_loop.py::test_prepare_run_durably_creates_state_and_publishes_received_before_execute -q`

Implementation: Added `RunSubmission` dataclass, `prepare_run()`, `execute()`, and `_execute_loop()` to `OrchestrationLoop`. Refactored `run()` to delegate to `prepare_run()` + `execute()`.

GREEN: 1 passed. Full suite: 35 passed.

### Slice 3 — live SSE answer.delta delivery
RED: `TypeError: create_app() got an unexpected keyword argument 'event_publisher'`
Command: `uv run --project services/app pytest services/app/tests/test_events.py::test_sse_endpoint_delivers_in_progress_answer_delta_before_terminal_event -q`

Implementation: Added `event_publisher` param to `create_app` and `create_events_router`. When `InMemoryEventPublisher` is injected, the durable-fallback path replays all run events (including transient `answer.delta`) before reconstructing from `RunStep` records.

GREEN: 1 passed. Full suite: 36 passed.

### Slice 4 — provider stream-error contract
RED→GREEN immediately: existing `BedrockLLMClient.stream_answer_with_query_result` already wraps mid-stream `ClientError` in `LLMProviderError`. Test confirms partial deltas emitted before the error are caller-observable; method raises, never returns partial text.

Command: `uv run --project services/app pytest services/app/tests/test_ask.py::test_bedrock_client_stream_raises_llm_provider_error_mid_stream_and_partial_deltas_are_emitted -q`
Result: 1 passed (GREEN on first run — implementation was already correct).

### Slice 5 — frontend progressive rendering + TTFT
RED: `TestingLibraryElementError: Unable to find JFK leads.` (App used `/api/ask`, not `/api/runs`)
RED: `TestingLibraryElementError: Unable to find "Available · 42 ms (provider_stream)"`

Implementation:
- `App.tsx` switched from `/api/ask` to `/api/runs` (run-first, 202 Accepted)
- Reads SSE stream: accumulates `answer.delta` into `streamingAnswer` state, shown progressively during loading
- On `run.completed`, sets `runTelemetry` from terminal event payload
- TTFT display: `"Available · <ms> ms (<source>)"` or `"Unavailable (non-streaming)"`
- `RunAccepted` type added; `RunTelemetry.ttft` now includes `latency_ms` and `source`

GREEN: 20 frontend tests passed, 37 backend tests passed.

## PR and merge state

- Issue: [#61](https://github.com/NakulManchanda/ai-analytics-poc/issues/61)
- Draft PR: [#62](https://github.com/NakulManchanda/ai-analytics-poc/pull/62)
- Commits pushed: f0728cc, 33fdbe2, 961019f
- Deployment and tag: separate post-merge milestone checkpoint.

## Limitations / unverified paths

- Frontend `/api/runs` SSE path parses the complete SSE response at once (via `fetch().text()`).
  Production EventSource or streaming fetch would accumulate chunks progressively; this test path
  exercises the accumulated output (correct for TestClient). True in-flight streaming in the browser
  would require using `ReadableStream` or `EventSource`, not tested here.
- Redis issue #57 transient delivery not tested in this slice; production must not silently assume
  localhost Redis (DynamoDB remains authoritative).


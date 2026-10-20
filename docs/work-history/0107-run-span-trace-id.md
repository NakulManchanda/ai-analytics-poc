# 0107 — `ai.run` span owned by `execute()` (#189 PR-A)

## Goal
capture.v1 `trace_id` was null for runs started through `POST /api/runs` even with OTEL enabled, so captures could not be joined to traces. Make every run, however dispatched, have exactly one `ai.run` span covering capture.

## Starting point
`ai.run` was created in the synchronous `OrchestrationLoop.run()` only. `/api/runs` calls `prepare_run` then `execute()` in a daemon thread with no span, and `capture_run` (in `execute()`'s `finally`) reads the current span, so `trace_id` was null. The LiteLLM and MCP clients already inject the current context, so an active span joins their spans too.

## Decisions
- The span moved into `execute()`; `run()` is now `prepare_run` + `execute`. Attributes are unchanged (`ai.run_id`, `ai.conversation_id`, `ai.turn_type`, `gen_ai.request.model`, `ai.status`). The span no longer covers `prepare_run` (negligible durable-write time).
- `capture_run` runs inside the span, so `_trace_id()` is valid.
- Fail-open: a tracer that raises on start/end/set-attribute is logged and the run proceeds without a span (trace_id null). Exceptions from the run itself still propagate and are recorded on the span.
- Worker threads already receive the context via `contextvars.copy_context()` in `_run_with_cancellation`; a test asserts an LLM call sees the run's trace id and `traceparent`.
- No FastAPI instrumentation, no backend-specific API (ADR 0007), no `eval_run_id` (separate PR).

## Verification
See the PR for exact command outcomes. New tests in `services/app/tests/test_orchestration_tracing.py`: async path capture trace id equals the span's; LLM call sees same trace/traceparent; no-op tracer gives null; broken tracer is fail-open. Existing single-span and exception tests pass unchanged.

## Limitations
No live Jaeger check yet; that is PR-D.

## Lessons
- Put a span where the shared work happens, not at one of its callers.

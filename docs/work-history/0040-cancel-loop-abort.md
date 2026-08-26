# 0040 — Orchestration Loop Cancellation Checkpoints and Bedrock Streaming Abort

## Goal
Implement Track 2 of Milestone v3 (Issue #75): propagate cancellation signals through the orchestration loop, abort active Bedrock `converse_stream` delta delivery, persist partial text into durable conversation state, and emit `run.cancelled` over SSE and Redis Streams.

## Starting Point
Track 1 (PR #78 / Issue #74) added `POST /api/runs/{run_id}/cancel` and the Redis fast-flag / durable state updates. However, the orchestration loop did not check cancellation between steps or during delta delivery.

## Decisions
- Defined `RunCancelledError(Exception)` for clean control flow abortion.
- Added `check_cancellation(partial_text)` checkpoints before schema loading, LLM proposal, MCP tool execution, context reduction, and final answer generation.
- Checked cancellation within `publish_answer_delta` during Bedrock streaming: halts generation on the first chunk following cancellation.
- Handled `RunCancelledError` in `_execute_loop`:
  - Persisted partial assistant message with `[interrupted]` suffix and `interrupted: True` metadata.
  - Updated durable `Run` status to `cancelled` with accurate partial token counts and latency.
  - Emitted `run.cancelled` terminal event.
- Added comprehensive unit tests in `services/app/tests/test_orchestration_cancellation.py`.

## Verification
- `uv run --project services/app pytest services/app/tests`: 108 passed.
- `make test`: all backend, MCP, dataset spike, infra, and frontend test suites passed.

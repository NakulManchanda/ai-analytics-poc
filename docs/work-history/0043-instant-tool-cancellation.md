# 0043 — Instant Async Tool-Call Cancellation and Fast Abort

## Goal
Implement sub-100ms instant cancellation for active runs even during long-running MCP DuckDB queries or LLM proposal calls (fixes #82).

## Starting Point
Milestone v3 (#74, #75, #76, #77) introduced cooperative cancellation checkpoints and Bedrock streaming abort. However, if a cancellation request arrived while DuckDB was executing an analytical scan (which takes up to ~10s over 2.96M rows), the loop blocked on the synchronous network socket before reaching the next checkpoint.

## Decisions
- Added `_run_with_cancellation(func, *args, run_id)` in `OrchestrationLoop` to execute blocking calls (schema discovery, LLM proposal, DuckDB tool queries) in a background thread while polling the Redis cancellation fast-flag every 50ms.
- When cancellation is detected:
  - Cancels the future and shuts down the executor without blocking on the worker thread (`shutdown(wait=False, cancel_futures=True)`).
  - Immediately raises `RunCancelledError`, aborting the loop in under 50ms.
- Emits `run.cancelled` immediately and cleanly terminates the SSE stream.
- Added unit test `test_cancellation_during_mcp_tool_execution` in `test_orchestration_cancellation.py` asserting that a 2.0s slow query aborts in <0.8s when cancelled.

## Verification
- `uv run --project services/app pytest services/app/tests/test_orchestration_cancellation.py`: 3 passed in 0.74s.
- `make test`: all backend, MCP, dataset spike, infra, and frontend test suites passed.

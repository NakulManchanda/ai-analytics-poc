# 0035 — Emit tool.failed and Surface Detailed Tool Errors in Timeline

## Goal
When a tool execution fails (e.g. MCP error, parameter validation failure, or execution exception), emit an explicit `tool.failed` event, record a failed `RunStep`, and pass the error message in the terminal `run.failed` payload so the Timeline Inspector and UI explicitly display the failure reason.

## Starting Point
Previously, when MCP tool execution threw an exception, `loop.py` caught it and immediately raised `OrchestrationError` without emitting a `tool.failed` event or recording a failed step. The UI remained stuck on `#tool.started` and `run.failed` lacked the detailed failure reason.

## Decisions
- Emitted `tool.failed` with `tool_name`, `tool_call_id`, `error`, and `duration_ms` when tool execution fails.
- Recorded a failed `RunStep` with `status="failed"` and output error summary in DynamoDB / StateRepository.
- Extended `terminal_run_payload` to accept `error` and pass detailed messages in `run.failed`.
- Updated `TimelineInspector.tsx` to include `tool.failed` in `KNOWN_EVENT_TYPES`, danger badge styling, and explicit error summary formatting.

## Verification
- `services/app/tests/test_events.py`: verified `tool.failed` emission, error details in `run.failed`, and failed step persistence in state repo.
- `web/src/TimelineInspector.test.tsx`: verified `tool.failed` and `run.failed` badge and summary rendering.
- `make test`: all backend, MCP, dataset spike, infra, and frontend tests passing.

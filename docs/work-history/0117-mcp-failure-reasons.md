# 0117 — Structured safe failure reasons for MCP tool errors (#209)

## Goal
When an MCP tool call fails, the run says why from a closed, safe set of reasons instead of only "MCP tool execution failed". In #208 the client row cap was invisible in the event, the app log and the MCP log.

## Starting point
`MCPToolError(retryable, message=None)` had about 25 raise sites in `services/app/app/mcp_client.py`, most without a message. `loop.py` wrapped it as `OrchestrationError("mcp_tool_error", ...)` at three sites and `run.failed` carried only `failure_code`/`error`/`retryable`. The replay in `routers/events.py` rebuilt `run.failed` without `provider_error` as well. The "No MCP client or factory configured" raise passed its message positionally into `retryable` (a `TypeError` at runtime).

## Decisions
- `MCP_FAILURE_REASONS` is a closed frozenset: `row_limit_exceeded`, `byte_limit_exceeded`, `invalid_result_shape`, `invalid_cell_value`, `tool_error_envelope`, `invalid_request`, `unknown_tool`, `transport_error`, `not_configured`, `unspecified` (default). `MCPToolError(..., reason=)` rejects anything else, and a test asserts every raise in `mcp_client.py` names a reason.
- `OrchestrationError.tool_error` (`{"reason", "tool_name"}`, tool name only when known) follows the `provider_error` pattern: `run.failed` payload via `terminal_run_payload` and durable run metadata, both omitted when there is no tool error, so existing payload shapes are unchanged.
- `tool.failed` gains `reason`; the failed tool step gains `metadata={"reason": ...}`.
- Replay passes `provider_error` and `tool_error` from run metadata; non-failed runs have neither key, so their payloads are unchanged.
- Fixed the positional-message bug with `message=` and `reason="not_configured"`.
- The byte cap still raises (`byte_limit_exceeded`); no compose-smoke failure case was added (a deterministic failing tool needs a fake MCP path that does not exist cheaply).
- Follow-ups not done: web `types.ts` field, eval report histograms by reason, capture-record schema.

## Verification
- Tests written first failed on the old code (the new test module could not import `MCP_FAILURE_REASONS`).
- `uv run --project services/app pytest services/app/tests -q`: 487 passed. black and ruff clean for `services/app`, `tests`, `services/gateway`. `make test` passes except `web-test` (no `node_modules` locally; `web/` untouched). `make compose-smoke` passed in its isolated project.

## PR / merge state
Branch `feat/209-mcp-failure-reasons`; PR #210.

## Lessons
- A reason on the exception costs one keyword per raise site; an AST test keeps new raise sites from silently defaulting to `unspecified`.

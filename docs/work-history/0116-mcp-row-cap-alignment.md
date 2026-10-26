# 0116 — MCP client row cap aligned with the validated limit (#186 Slice B)

## Goal
Golden det-006 ("Compare weekday and weekend trip volume by hour", a catalogue hit with no planner call) must return its complete 48-row result instead of `run.failed` (`mcp_tool_error`).

## Starting point
The run ended `step.tool_call` failed with the generic "MCP tool execution failed". Probes showed the MCP server and dataset layer return all 48 rows at limit 48/50; the rejection came from `sanitize_query_result` in `services/app/app/mcp_client.py`, which raised `MCPToolError(retryable=False)` on `len(rows) > 20`. The planner schemas and `validation/proposal.py` allow limits up to `MAX_ROWS = 64`, and the catalogue entry used `limit: 50` (#193) so 48 rows would not be truncated. Any model-planned aggregate/compare call with 21+ result rows failed the same way.

## Decisions
- `mcp_client.MAX_QUERY_RESULT_ROWS` is now `validation.proposal.MAX_ROWS` (no import cycle: `validation/proposal.py` imports nothing from the app). `query_taxi_data` keeps its own request limit of 20.
- MCP server, dataset layer and the byte cap (`MAX_QUERY_RESULT_BYTES`) are unchanged; a 48-row, 3-column result is far below it.
- Tests cross the layers: the sanitizer accepts `MAX_ROWS` rows and rejects `MAX_ROWS + 1`, the client cap equals the validator's `MAX_ROWS`, and every governed catalogue entry's `limit` (capped at 48 for det-006) passes `sanitize_governed_query_result`.
- `scripts/smoke/03_compose_ui.sh` now runs every catalogue prompt (keys read from `query_catalogue.py`) through `/api/runs` against the real MCP service and requires `run.completed` with a tool call. All 20 prompts complete on the CI dataset.
- `LocalFakeLLMClient.answer_with_query_result` raised for any result row that was not 2 columns (or the one 4-column shape), so aggregate/compare results failed the smoke at the answer step once the tool succeeded. It now returns a generic row-count answer for other shapes (test double only).
- Not done: the generic "MCP tool execution failed" message still hides the cause; the byte cap still raises rather than truncates; det-007/det-013 dimensions, react-001 multi-step and #207 are separate.

## Verification
- Before the fix: the new unit tests failed (`MCPToolError` from `len(rows) > 20`), and `make compose-smoke` failed at "compare weekday and weekend trip volume by hour" with events `run.received, context.loading, tool.requested, tool.started, tool.failed, run.failed`.
- After the cap fix the smoke failed at the fake LLM answer step (`LLMProviderError`), which led to the fake-client change.
- `uv run --project services/app pytest services/app/tests -q`: 467 passed.
- `make compose-smoke`: passes with all 20 catalogue prompts. Two earlier attempts failed only because the app container healthcheck (10 x 1s) timed out under heavy host load; the rerun passed.

## PR / merge state
Branch `fix/186-mcp-catalogue-error`; PR number TBD.

## Lessons
- Three layers each had a self-consistent test (catalogue vs validator, planner schema vs validator, client sanitizer vs its own cap) and none crossed layers, so a contract mismatch between them shipped.
- A smoke that covers every catalogue prompt on the real stack is cheap (no model spend) and catches this class of mismatch.
- A fake provider that only knows a few result shapes turns a real fix into a second smoke failure; fakes should degrade generically.

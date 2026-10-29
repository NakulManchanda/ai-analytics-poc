# 0122 — Phoenix trace hygiene: opt-in tool results, no status-poll traces (#216)

## Goal
Two follow-ups from the retrospective review of #214: let a human see the rows a tool returned on its MCP span without making that the default, and stop the UI's status polling from filling Phoenix with one-span traces.

## Starting point
MCP spans carried only the tool name, `ai.row_count` and `ai.truncated`; every discovery request without a caller trace became its own trace (6 stray traces for 2 real runs).

## Decisions
- `MCP_TRACE_RESULTS=1` (default off, passed through `docker-compose.phoenix.yml`) attaches `ai.result.preview` / `preview_rows` / `preview_truncated` to `duckdb.query`, capped by `MCP_TRACE_RESULT_MAX_BYTES` (4096 default, 256..65536) by dropping trailing whole rows. The tool result is never trimmed; the helper never raises.
- `MCPTracingMiddleware` does not create a span for `initialize`, `tools/list`, `resources/list`, `resources/templates/list`, `prompts/list` or `ping` when there is no valid caller trace. With a traceparent they are traced as before; `tools/call` and `resources/read` are always traced.
- Out of scope: the other review items (redacted mode, exporter-failure smoke, OTEL routing doc), annotation workflow (#190), the app-side status endpoint.

## Verification
- `uv run pytest -q` in `services/mcp`: all pass (existing root-span tests for a bare `initialize` now assert no span; new tests for suppression, still-traced work requests, preview off/on/truncation/clamping/no-rows).
- Live (isolated stack on ports 3400/14400/16406, `MCP_TRACE_RESULTS=1`, no model calls): seven `/api/status` polls produced no traces; one direct `aggregate_taxi_data` call produced a single trace (`mcp.request`, `mcp.tool.execute`, `duckdb.query`) whose `duckdb.query` carried `ai.result.preview` (8 rows, 190 bytes), `preview_rows=8`, `preview_truncated=false`. Truncation is covered by unit tests only.

## PR / merge state
Branch `feat/216-phoenix-trace-hygiene`.

## Lessons
- The status poll is the only source of rootless discovery requests; keying suppression on "no caller trace" leaves real runs' discovery spans intact.

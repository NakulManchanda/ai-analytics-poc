# 0036 — Constrain average_trip_metrics Borough Enum and Propagate Validation Messages

## Goal
Constrain `region_name` in Bedrock's `average_trip_metrics` toolSpec to the governed NYC borough enum (`Manhattan`, `Brooklyn`, `Queens`, `Bronx`, `Staten Island`) with instructions that omitting it compares all 5 boroughs. Also ensure `MCPToolError` preserves and surfaces the exact error message from FastMCP server responses.

## Starting Point
When asked to compare all boroughs, Bedrock passed `region_name="major pickup boroughs"`, which FastMCP rejected with `RegionValidationError` because `region_name` was unconstrained. Furthermore, `mcp_client.py` dropped the detailed error message string.

## Decisions
- Updated `average_trip_metrics` toolSpec in `services/app/app/llm.py` to specify `enum: ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]` and clarified that `region_name` is optional.
- Updated `_average_trip_metrics` in `services/app/app/mcp_client.py` to extract `error.get("message")` and include it in `MCPToolError`.
- Added unit test in `services/app/tests/test_ask.py` asserting the toolSpec schema and enum.

## Verification
- `uv run --project services/app pytest services/app/tests`: 98 passed.
- `make test`: all backend, MCP, dataset spike, infra, and frontend tests passed.

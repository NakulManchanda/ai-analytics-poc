# Current milestone

Milestone 6 — governed read-only analytical query tool

## Status

IN PROGRESS — [issue #24](https://github.com/NakulManchanda/ai-analytics-poc/issues/24)

## Merged milestone baseline

- **Milestone 0**: Minimal FastAPI `GET /health` endpoint (merged).
- **Milestone 1**: Independently runnable empty FastMCP service on port 8001 (merged).
- **Milestone 2**: NYC TLC Parquet dataset profile via DuckDB and FastMCP tools/resources (merged, PR #17).
- **Milestone 3**: Minimal React status shell with same-origin `/api/` proxy and Compose smoke (merged, PR #19).
- **Milestone 4**: Application-owned bounded Bedrock call (merged, PR #15).
- **Milestone 5**: Fixed two-LLM/one-MCP dataset-profile flow with a usable prompt UI (merged, PR #23).
- **Milestone 13 Foundation**: Terraform infrastructure foundation (merged, PR #3 / PR #11) and optional AWS Budget alerts (merged, PR #20). The budget configuration has not been applied to AWS.

## Acceptance criteria

- [ ] `query_taxi_data` accepts only an allowlisted structured analysis request; callers never supply SQL.
- [ ] DuckDB execution is SELECT-only, single-statement, allowlisted, row/byte/time bounded, and blocks filesystem/network access.
- [ ] The app supplies the governed dataset/schema context to the first model call.
- [ ] Three deterministic starter questions complete question → LLM → MCP → DuckDB → bounded result → answer.
- [ ] Fake-backed acceptance/rejection tests and an explicitly opt-in real Bedrock smoke pass.
- [ ] Draft PR, work history, documentation, CI, and Copilot review are complete.

## Decisions

- Use an allowlisted structured analysis enum rather than model-authored or user-authored SQL.
- Keep the M5 fixed two-model-call/one-tool-call sequence; M6 does not introduce a general loop.
- Read the schema through the MCP resource before proposal generation, then validate the exact structured request before execution.

## Known limitations

- M6 has no persistence, Redis/SSE events, async jobs, general loop, or arbitrary user SQL.
- Aggregate run budgets remain Milestone 8 work; this slice stays fixed to two model calls and one governed query tool call.

## Next milestone

Do not start Milestone 7 until requested.

# Current milestone

Milestone 5 — one-turn LLM-to-MCP tool execution

## Status

IN PROGRESS — [issue #22](https://github.com/NakulManchanda/ai-analytics-poc/issues/22)

## Merged milestone baseline

- **Milestone 0**: Minimal FastAPI `GET /health` endpoint (merged).
- **Milestone 1**: Independently runnable empty FastMCP service on port 8001 (merged).
- **Milestone 2**: NYC TLC Parquet dataset profile via DuckDB and FastMCP tools/resources (merged, PR #17).
- **Milestone 3**: Minimal React status shell with same-origin `/api/` proxy and Compose smoke (merged, PR #19).
- **Milestone 13 Foundation**: Terraform infrastructure foundation (merged, PR #3 / PR #11) and optional AWS Budget alerts (merged, PR #20). The budget configuration has not been applied to AWS.

## Acceptance criteria

- [ ] First LLM call receives only the fixed `get_dataset_profile` tool contract.
- [ ] An exact no-argument proposal is validated before any MCP execution.
- [ ] The application calls the existing FastMCP HTTP service exactly once and bounds/sanitizes its result.
- [ ] The second LLM call receives the bounded tool result and returns the final answer.
- [ ] The route allows at most two LLM calls and one MCP call; it has no general loop.
- [ ] The browser prompt shows loading, controlled errors, final answer, and bounded usage metadata.
- [ ] Normal tests use fakes; the real Bedrock smoke remains explicitly opt-in and paid.
- [ ] Draft PR, work history, documentation, CI, and Copilot review are complete.

## Decisions

- M5 is a deliberately fixed sequence, not the future general orchestration loop.
- Only the application invokes Bedrock; FastMCP remains a read-only, LLM-free tool boundary.
- Tool proposals other than the exact no-argument dataset profile contract fail closed.

## Known limitations

- M5 has no model-generated SQL, persistent conversation/run records, Redis/SSE events, async jobs, or arbitrary model-rendered UI.
- Aggregate loop, time, token, and cost budgets remain Milestone 8 work; this slice permits exactly two configured calls.

## Next milestone

Do not start Milestone 6 until requested.

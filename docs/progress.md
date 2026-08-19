# Current milestone

Milestone 7 — durable conversation and run persistence

## Status

IN PROGRESS — [issue #18](https://github.com/NakulManchanda/ai-analytics-poc/issues/18)

## Merged milestone baseline

- **Milestone 0**: Minimal FastAPI `GET /health` endpoint (merged).
- **Milestone 1**: Independently runnable empty FastMCP service on port 8001 (merged).
- **Milestone 2**: NYC TLC Parquet dataset profile via DuckDB and FastMCP tools/resources (merged, PR #17).
- **Milestone 3**: Minimal React status shell with same-origin `/api/` proxy and Compose smoke (merged, PR #19).
- **Milestone 4**: Application-owned bounded Bedrock call (merged, PR #15).
- **Milestone 5**: Fixed two-LLM/one-MCP dataset-profile flow with a usable prompt UI (merged, PR #23).
- **Milestone 6**: Governed read-only `query_taxi_data` tool with allowlisted analysis and Bedrock integration (merged, PR #33).
- **Milestone 13 Foundation**: Terraform infrastructure foundation (merged, PR #3 / PR #11) and optional AWS Budget alerts (merged, PR #20).

## Acceptance criteria

- [ ] Domain models defined for `Conversation`, `Message`, `Run`, and `RunStep`.
- [ ] Distinct IDs: `conversation_id`, `message_id`, `run_id`, `step_id`, `llm_call_id`, `tool_call_id`, `query_id`.
- [ ] DynamoDB adapter compatible with single-table schema (`pk`, `sk`) and default AWS credential chain / task role.
- [ ] Conditional/idempotent writes and required ordering/pagination implemented.
- [ ] Application state survives process restart (reconstructed from DynamoDB).
- [ ] Unit tests use fakes/stubs and never mutate live AWS.
- [ ] No orchestration loop (M8), Bedrock changes, MCP changes, HTTP wiring, Redis, or Terraform apply.

## Decisions

- Single-table partition/sort key layout (`pk`, `sk`) aligning with provisioned `aws_dynamodb_table.application_state`.
- Domain models and repository interfaces kept strictly isolated in `services/app/app/state/` to avoid premature endpoint coupling.
- Float/Decimal conversions handled gracefully for token usage and estimated cost metrics.

## Known limitations

- M7 does not wire the repository into the `/api/ask` HTTP endpoint yet (that is M8 orchestration loop).
- M7 does not add Redis Streams or SSE event publishing (that is M9).
- M7 does not store raw analytical query results or large payloads in DynamoDB.

## Next milestone

Do not start Milestone 8 until Milestone 7 is merged.

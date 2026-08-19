# Current milestone

Milestone 4 — first Bedrock call owned by the application

## Status

IMPLEMENTED — awaiting review and merge in [PR #15](https://github.com/NakulManchanda/ai-analytics-poc/pull/15)

## Merged milestone baseline

- **Milestone 0**: Minimal FastAPI `GET /health` endpoint (merged).
- **Milestone 1**: Independently runnable empty FastMCP service on port 8001 (merged).
- **Milestone 2**: NYC TLC Parquet dataset profile via DuckDB and FastMCP tools/resources (merged, PR #17).
- **Milestone 3**: Minimal React status shell with same-origin `/api/` proxy and Compose smoke (merged, PR #19).
- **Milestone 13 Foundation**: Terraform infrastructure foundation (merged, PR #3 / PR #11) and optional AWS Budget alerts (merged, PR #20). The budget configuration has not been applied to AWS.

## Acceptance criteria (PR #15 — Bedrock single call)

- [x] `POST /api/ask` makes one configured Amazon Bedrock Converse call from `ai-app` only.
- [x] An explicit `LLMClient` boundary supports fake-client endpoint and Bedrock-response unit tests.
- [x] Responses expose an opaque per-call `llm_call_id`, model ID, input/output/total token usage, and Bedrock latency metadata.
- [x] Expected provider and configuration failures become non-leaking retryable/non-retryable API errors.
- [x] The app and Terraform validate the one allowed `us-east-1` `amazon.nova-micro-v1:0` model/ARN alignment; malformed and whitespace-only prompts are rejected.
- [x] The real smoke path is opt-in, bounded to one call with a 128 output-token maximum, and fails closed unless its full response contract is met.
- [x] Terraform grants `bedrock:InvokeModel` for only that foundation-model ARN to `ai-app`; `analytics-mcp` has no Bedrock policy.
- [x] No tools, loop, UI, persistence, Redis, or MCP execution were introduced.
- [ ] Pull request reviewed and merged.

## Decisions

- The application owns all model calls through `LLMClient`; the Bedrock SDK client is lazy so fake-client tests never resolve AWS credentials or incur cost.
- `llm_call_id` is generated at the route boundary for every request, so successful and controlled-error responses can be correlated without exposing provider diagnostics.
- M4 returns only the metadata that a later durable run and usage event will need. It does not pre-create run IDs, events, budgets, or state.
- The M4 model is locked to the Bedrock foundation-model ARN in `us-east-1`; no Terraform apply is performed in this milestone.

## Known limitations

- The single synchronous call has only per-request (4,000 characters) and per-call (128 output tokens) bounds. Aggregate time, token, cost, and concurrency budgets belong to the later orchestration milestone.
- The manual Bedrock smoke needs AWS credentials available through the local default provider chain and incurs the selected model's on-demand cost. Deployment is intentionally limited to the selected M4 region/model until a later milestone expands the allowlist deliberately.

## Next milestone

Next milestone: Milestone 5 — one-turn LLM-to-MCP tool execution.

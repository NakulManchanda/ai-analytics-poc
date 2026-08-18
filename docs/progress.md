# Current milestone

Milestone 4 — first Bedrock call owned by the application

## Status

IMPLEMENTED — awaiting review and merge in [PR #15](https://github.com/NakulManchanda/ai-analytics-poc/pull/15)

## Acceptance criteria

- [x] `POST /api/ask` makes one configured Amazon Bedrock Converse call from `ai-app` only.
- [x] An explicit `LLMClient` boundary supports fake-client endpoint and Bedrock-response unit tests.
- [x] Responses expose model ID, input/output token usage, and Bedrock latency metadata.
- [x] The default model is `amazon.nova-micro-v1:0`; malformed and whitespace-only prompts are rejected.
- [x] The real smoke path is opt-in and bounded to one call with a 128 output-token maximum.
- [x] Terraform grants `bedrock:InvokeModel` for only that foundation-model ARN to `ai-app`; `analytics-mcp` has no Bedrock policy.
- [x] No tools, loop, UI, persistence, Redis, or MCP execution were introduced.
- [ ] Pull request reviewed and merged.

## Decisions

- The application owns all model calls through `LLMClient`; the Bedrock SDK client is lazy so fake-client tests never resolve AWS credentials or incur cost.
- M4 returns only the metadata that a later durable run and usage event will need. It does not pre-create run IDs, events, budgets, or state.
- The M4 model is locked to the Bedrock foundation-model ARN in `us-east-1`; no Terraform apply is performed in this milestone.

## Known limitations

- The single synchronous call has only per-request (4,000 characters) and per-call (128 output tokens) bounds. Aggregate time, token, cost, and concurrency budgets belong to the later orchestration milestone.
- The manual Bedrock smoke needs AWS credentials available through the local default provider chain and incurs the selected model's on-demand cost.

## Next milestone

Next milestone: Milestone 5 — one-turn LLM-to-MCP tool execution.

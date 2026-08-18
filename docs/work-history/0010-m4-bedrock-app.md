# Work 0010 — Milestone 4 application-owned Bedrock call

## Goal

Add the smallest reviewable Bedrock vertical slice: one `POST /api/ask` request handled by the
FastAPI application through an explicit LLM client boundary, with no tool execution or loop.

## Starting state

`origin/main` at `053b43e` (merged PR #12) contained Milestones 0–2: a health endpoint, an empty
MCP service, and a standalone dataset spike. The app had no LLM dependency or `/api/ask` route.

## Decisions

- Use `amazon.nova-micro-v1:0` as the explicit default `LLM_MODEL_ID` and use Bedrock's
  non-streaming `Converse` API with a maximum of 128 output tokens.
- Keep the Bedrock SDK behind an `LLMClient` protocol. The endpoint and response-mapping tests use
  fake clients; the real SDK client is created only on the first requested call.
- Expose answer, model ID, input/output tokens, and provider latency. Future run state and SSE
  usage events can consume those fields, but M4 creates no IDs, durable records, Redis events, or
  budgets beyond its per-call/request caps.
- Restrict `ai-app` Terraform permission to `bedrock:InvokeModel` on only the selected
  `us-east-1` foundation-model ARN. The MCP role remains without a Bedrock policy.

## Verification

- Initial TDD red: `uv run --project services/app pytest services/app/tests/test_ask.py -q` failed
  because `create_app()` did not yet accept an injected LLM client.
- `uv run --project services/app pytest services/app/tests -q` passed 4 tests; `ruff` and `black`
  both passed. `make test` also passed the M0 app suite (4 tests), M1 MCP suite (1 test; an
  upstream FastMCP deprecation warning), and M2 fixture suite (5 tests).
- `terraform fmt -check -recursive infra/terraform` and backendless `terraform validate` passed;
  no Terraform plan or apply ran. `RUN_BEDROCK_SMOKE=0` correctly refused the paid smoke call.
- `make smoke` passed the health endpoint, empty-MCP protocol check, and cached dataset profile.
  `make bedrock-smoke` made one call through the endpoint and returned `BEDROCK_SMOKE_OK` from
  `amazon.nova-micro-v1:0` with 11 input tokens, 8 output tokens, and 279 ms latency.

## Pull request and merge state

Draft [PR #15](https://github.com/NakulManchanda/ai-analytics-poc/pull/15), branch
`feat/m4-bedrock-app`, tracks [issue #14](https://github.com/NakulManchanda/ai-analytics-poc/issues/14).
It is not merged and no Terraform apply was run.

## Lessons

- Construct the ask router per application instance: a module-global router can capture the first
  application's default client and bypass test injection.
- Per-call caps are useful early cost guards but are not substitutes for the durable, aggregate
  run budgets planned for the orchestration milestone.

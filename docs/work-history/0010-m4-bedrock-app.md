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
- Generate an opaque `llm_call_id` at the application route boundary and return it with successful
  and controlled-error responses. This creates a later-compatible correlation point without
  adding run persistence or event streams.
- Convert expected Bedrock SDK failures into typed client errors and map them to generic API error
  codes with explicit retryability. Provider exception messages never reach callers.
- Restrict `ai-app` Terraform permission to `bedrock:InvokeModel` on only the selected
  `us-east-1` foundation-model ARN. App configuration and Terraform input validation both reject
  a different region or model. The MCP role remains without a Bedrock policy.

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
- Exact-head follow-up after merging `origin/main` at `219608b`: the app suite passed 10 tests,
  including deterministic call IDs, retryable/non-retryable non-leaking provider errors, and
  invalid region/model configuration. `make test` passed app (10), MCP (1; existing FastMCP
  deprecation warning), and dataset (5) suites. Terraform validates the valid configuration and
  its expression evaluator reports the expected errors for a non-`us-east-1` region and a
  non-Nova-Micro ARN. The merged M2 MCP dataset smoke passed, as did the cumulative smoke.
- The refreshed one-call `make bedrock-smoke` returned `BEDROCK_SMOKE_OK` with an opaque
  `llm_call_id`, 11 input tokens, 8 output tokens, and 322 ms latency.
- Exact-head smoke hardening after merging `origin/main` at `a56b3c4`: a regression test accepts
  only the expected answer plus typed call/model/usage/latency fields, and rejects a bad answer,
  malformed token count, or incorrect total. The endpoint now returns `total_tokens` derived from
  the provider's input/output counts; the opt-in script exits nonzero when that contract is not met.
- The smoke-contract tests were written red before the validator existed and before
  `total_tokens` was returned. The merged full suite passed app (17), MCP (1; existing FastMCP
  deprecation warning), dataset (5), and React (2) tests plus the production browser build.
  Backend cumulative and Compose UI smokes passed. The refreshed paid smoke returned
  `BEDROCK_SMOKE_OK`, a generated `llm_call_id`, 11 input, 8 output, 19 total tokens, and 507 ms.

## Pull request and merge state

Draft [PR #15](https://github.com/NakulManchanda/ai-analytics-poc/pull/15), branch
`feat/m4-bedrock-app`, tracks [issue #14](https://github.com/NakulManchanda/ai-analytics-poc/issues/14).
It is not merged and no Terraform apply was run.

## Lessons

- Construct the ask router per application instance: a module-global router can capture the first
  application's default client and bypass test injection.
- Per-call caps are useful early cost guards but are not substitutes for the durable, aggregate
  run budgets planned for the orchestration milestone.
- SDK exception handling belongs behind the application boundary; doing it there keeps the API
  stable while retaining provider-specific behavior in the client.

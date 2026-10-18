# Work 0094 — LiteLLM Bedrock-first model proxy

## Milestone

Epic #167, issue #168 (1 of 4)

## Branch

`feat/168-litellm-proxy`

## Worktree

`.worktrees/168-litellm-proxy`

## Goal

Make LiteLLM Proxy the default Bedrock model path behind `LLMClient`, with aliases, observability, and a rollback.

## Starting state

No LiteLLM code; ADR 0010 deferred it. Bedrock is the main path, so two gateways (LiteLLM and the GPU gateway) are acceptable.

## Decisions

- ADR 0012 supersedes the deferral. LiteLLM is the default in local Compose (`make local-aws-litellm-compose`) and Terraform (ai-app `LLM_PROVIDER=litellm`, internal ECS service, Bedrock-only task role, Secrets Manager master key). Terraform validated, not applied.
- Deployed proxy is stateless. Local Compose adds Postgres, the Admin UI (`127.0.0.1:14000`), DEBUG logs, and prompt/response storage.
- Master key lives in the git-ignored `.env` (`make litellm-env`), never printed.
- `LiteLLMClient` subclasses `ServeLLMClient` via a hook; planner alias for proposals, answer alias otherwise; conversation id sent as `x-litellm-session-id`.
- Review fixes: `LiteLLMClient` reserves the shared DynamoDB Bedrock allowance once per proxy request (cap preserved on the default path); `LITELLM_API_KEY` is required when `LLM_PROVIDER=litellm`; local prompt/response capture and DEBUG logs are opt-in (`LITELLM_CAPTURE=1`).
- No `gateway_chat` bypass test: the replayer only drives vLLM gateway load tests.

## Bugs found by running it

- The loop treated `LiteLLMClient` as self-hosted (cost 0). Now billed as `bedrock_estimate`.
- Forwarding stored vLLM-shaped conversation history made Bedrock reject turn 2. Only the conversation id is forwarded now (direct Bedrock never sent history).
- Planner reliability: through the proxy, tool proposals succeeded 5/10 and 7/10 against 10/10 direct. Cause: the proxy path used the vLLM prompt and `tool_choice: auto`. `LiteLLMClient` now reuses the Bedrock proposal prompt and tool schemas (shared module constants), forces tool use, and uses temperature 0. Result: 10/10 on both prompts, matching direct.

## Verification

- `make test`: app 234, plus bench/gateway/MCP/dataset/infra suites passed; `web-test` 41 passed and build ok (after `npm ci`); Black, ruff, terraform fmt/validate clean.
- Live, real Bedrock: proxy readiness healthy with DB; `make litellm-smoke` completes a run and finds its planner+answer calls by session; `make litellm-calls/spend/errors` read the logs.
- Rollback smoke: `make local-bedrock-direct-compose` run completed with provider `bedrock` (no proxy calls).
- Trace context: the proxy adopted a supplied `traceparent` trace id (0af76519…).
- Streaming: answer streamed through the proxy; run telemetry recorded a real `ttft_ms` (the non-streaming path reports an unavailable reason instead).
- Not done: live budget-exhaustion against DynamoDB (unit-tested only); committed direct-vs-proxy contract test; cancellation/deadline tests through the proxy; terraform plan/apply; an OTEL exporter to a collector; catalogue prompts skip the planner by design, so they log only an answer call.

## PR / merge state

Draft PR #172.

## Lessons

- Run real multi-turn traffic early: unit tests did not catch the history, cost-label, or planner-parity issues.
- Compare a new provider path against the old one on the same prompts before calling it equivalent.

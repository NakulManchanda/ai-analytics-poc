# LiteLLM Proxy (default model path, Bedrock-first)

Decision: [ADR 0012](decisions/0012-litellm-bedrock-model-proxy.md). Issue #168, epic #167.

## What it is

A separate LiteLLM Proxy service, now the default model path locally (`make local-aws-litellm-compose`) and in Terraform (ai-app `LLM_PROVIDER=litellm`). The app (`LiteLLMClient`, `LLM_PROVIDER=litellm`) calls
stable aliases; the proxy maps them to Bedrock. The app still owns every LLM call, loop budget and
durable state. Deployed (ECS) proxy is stateless. Local Compose adds Postgres so the Admin UI and call logs work.

| Alias | Used for | Target |
| --- | --- | --- |
| `taxi-planner` | tool proposal calls | Bedrock Nova Micro |
| `taxi-answer` | answers (streaming and not), `ask` | Bedrock Nova Micro |
| `taxi-classifier` | reserved for later slices | Bedrock Nova Micro |

Config: `infra/litellm/config.yaml` (image pinned in `docker-compose.litellm.yml`).

## Run locally

```bash
make local-aws-litellm-compose   # default; proxy + Postgres + app on real AWS
make litellm-ui                  # Admin UI: http://127.0.0.1:14000/ui (admin / LITELLM_MASTER_KEY from .env)
make litellm-traffic             # follow every intercepted request/response (DEBUG logs)
make litellm-ready               # readiness check
make litellm-spend               # per-alias calls, failures, tokens, cost
make litellm-errors              # failed calls with the provider error
make litellm-env                 # generate LITELLM_MASTER_KEY into git-ignored .env (value never printed)
make litellm-calls               # compact table of recent proxy calls (needs jq); SESSION=conv_... LIMIT=20
make litellm-smoke               # real run through the app; asserts the proxy logged its calls (spends a few tokens)
make local-bedrock-direct-compose  # rollback: direct Bedrock, no proxy
```

`LITELLM_PORT` overrides the host port (default 14000, loopback only). `local-aws-compose` remains as a deprecated alias.
Call metadata is stored in Postgres; read it via the UI or `GET /spend/logs`. Bodies are stored only with `LITELLM_CAPTURE=1`.

## Terraform

`infra/terraform/litellm.tf` runs the proxy as an internal ECS service (Service Connect name `litellm`), with a
dedicated Bedrock-only task role and a Secrets Manager master key. Not applied by this PR.

## Secrets and identity

- `LITELLM_MASTER_KEY` is supplied via the environment only; never commit it.
- Bedrock credentials come from the AWS SDK chain (mounted `~/.aws` locally, a task/instance role on AWS),
  the same identity and model allowlist as the direct client. Config contains only `bedrock/` models, so
  there is no path to a public provider.
- `LITELLM_API_KEY` in the app is the proxy key it presents; `LITELLM_PROXY_URL`,
  `LITELLM_PLANNER_ALIAS`, `LITELLM_ANSWER_ALIAS` override defaults.

## Correlation headers (#189)

The app sends these headers so a proxy spend-log row can be mapped to an app run and, 1:1, to a capture `call_id`:

| Header | Sent when | Lands in spend logs as |
|---|---|---|
| `x-litellm-session-id` | a conversation id exists | `session_id` |
| `x-litellm-spend-logs-metadata` | a run id or call id exists (every orchestrated model call, not eval-only) | `metadata.spend_logs_metadata` = `{"run_id","call_id","attempt","eval_run_id"?}` |
| `x-litellm-tags: eval_run_id:<id>` | only when the run has an `eval_run_id` | `request_tags` |

- `call_id` is the app's `llm_call_id`, identical to the capture `call_id`. `attempt` is the 1-based physical proxy request within that logical call (policy fallbacks on transient errors issue several), so `(call_id, attempt)` identifies one spend row. The proxy's own `request_id` stays unrelated.
- Calls outside a run (for example `ask`) send no correlation headers. Header construction is fail-open: an error is logged and the headers are omitted; the call is never failed.
- `eval_run_id` is a correlation label only. It is not an authorization, tenant or billing boundary; budgets and entitlements never read it.

## Observability

- Readiness: `GET /health/readiness` (Compose healthcheck); liveness `GET /health/liveliness`.
- The client injects W3C `traceparent` so proxy spans join the app trace; the proxy `otel` callback exports
  via standard `OTEL_*` env vars.
- The selected alias is the `model` in the request and the response's `model` field.

## Operational dependency and rollback

With the flag on, the proxy is a hard dependency for model calls (a down proxy maps to retryable
`litellm_proxy_unavailable`; HTTP 503/504/529 retryable, other statuses not).

Rollback: locally use `make local-bedrock-direct-compose`; in Terraform set ai-app `LLM_PROVIDER` back to `bedrock` (its task role keeps Bedrock access). The GPU gateway and the direct `gateway_chat` replayer never use the proxy.

## Spend guard

`LiteLLMClient` reserves the shared DynamoDB Bedrock allowance (`DynamoDBBedrockBudget`) once per proxy
request, exactly like the direct client, so the monthly cap still applies on the default path. An exhausted
or unavailable allowance maps to `bedrock_budget_exhausted` / `bedrock_budget_unavailable` before the proxy is
called. `LITELLM_API_KEY` is required when `LLM_PROVIDER=litellm` (startup fails closed without it).
Proxy-native budgets and per-key limits are #170.

## Prompt capture is opt-in

Local logging is metadata-only by default. `LITELLM_CAPTURE=1 make local-aws-litellm-compose` stores prompts and
responses in Postgres and prints bodies (DEBUG) for evaluation capture; without it `make litellm-calls` shows `-`
in the TOOL column and Logs has no request/response JSON. Deployed ECS never stores bodies.

## Known limitations

- Terraform is validated only (no plan/apply); the ECS rollout is unverified.
- OTEL: the proxy adopts the incoming `traceparent`, but no collector/exporter is configured for it yet.
- Cancellation and deadline behaviour through the proxy have no proxy-specific tests.

# LiteLLM Proxy (default model path, Bedrock-first)

Decision: [ADR 0012](decisions/0012-litellm-bedrock-model-proxy.md). Issue #168, epic #167.

## What it is

A separate LiteLLM Proxy service, now the default model path locally (`make local-aws-litellm-compose`) and in Terraform (ai-app `LLM_PROVIDER=litellm`). The app (`LiteLLMClient`, `LLM_PROVIDER=litellm`) calls
stable aliases; the proxy maps them to Bedrock. The app still owns every LLM call, loop budget and
durable state. Deployed (ECS) proxy is stateless. Local Compose adds Postgres so the Admin UI and request/response logs work.

| Alias | Used for | Target |
| --- | --- | --- |
| `taxi-planner` | tool proposal calls | Bedrock Nova Micro |
| `taxi-answer` | answers (streaming and not), `ask` | Bedrock Nova Micro |
| `taxi-classifier` | reserved for later slices | Bedrock Nova Micro |

Config: `infra/litellm/config.yaml` (image pinned in `docker-compose.litellm.yml`).

## Run locally

```bash
make local-aws-litellm-compose   # default; proxy + Postgres + app on real AWS
make litellm-ui                  # Admin UI: http://127.0.0.1:14000/ui (admin / LITELLM_MASTER_KEY, default sk-local-dev)
make litellm-traffic             # follow every intercepted request/response (DEBUG logs)
make litellm-ready               # readiness check
make litellm-calls               # compact table of recent proxy calls (needs jq); SESSION=conv_... LIMIT=20
make litellm-smoke               # real run through the app; asserts the proxy logged its calls (spends a few tokens)
make local-bedrock-direct-compose  # rollback: direct Bedrock, no proxy
```

`LITELLM_PORT` overrides the host port (default 14000, loopback only). `local-aws-compose` remains as a deprecated alias.
Local stores prompts/responses in Postgres (`STORE_PROMPTS_IN_SPEND_LOGS`); read them via the UI or `GET /spend/logs`.

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

## Observability

- Readiness: `GET /health/readiness` (Compose healthcheck); liveness `GET /health/liveliness`.
- The client injects W3C `traceparent` so proxy spans join the app trace; the proxy `otel` callback exports
  via standard `OTEL_*` env vars.
- The selected alias is the `model` in the request and the response's `model` field.

## Operational dependency and rollback

With the flag on, the proxy is a hard dependency for model calls (a down proxy maps to retryable
`litellm_proxy_unavailable`; HTTP 503/504/529 retryable, other statuses not).

Rollback: locally use `make local-bedrock-direct-compose`; in Terraform set ai-app `LLM_PROVIDER` back to `bedrock` (its task role keeps Bedrock access). The GPU gateway and the direct `gateway_chat` replayer never use the proxy.

## Known limitations

- Rollback smoke and direct-vs-proxy live comparison are not yet run.
- Budget enforcement for the proxy path (`DynamoDBBedrockBudget`) is not applied; spend controls are #170.

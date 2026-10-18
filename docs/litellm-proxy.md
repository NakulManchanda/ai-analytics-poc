# LiteLLM Proxy (opt-in, Bedrock-first)

Decision: [ADR 0012](decisions/0012-litellm-bedrock-model-proxy.md). Issue #168, epic #167.

## What it is

A separate, stateless LiteLLM Proxy service. The app (`LiteLLMClient`, `LLM_PROVIDER=litellm`) calls
stable aliases; the proxy maps them to Bedrock. The app still owns every LLM call, loop budget and
durable state. No database and no Admin UI in this slice.

| Alias | Used for | Target |
| --- | --- | --- |
| `taxi-planner` | tool proposal calls | Bedrock Nova Micro |
| `taxi-answer` | answers (streaming and not), `ask` | Bedrock Nova Micro |
| `taxi-classifier` | reserved for later slices | Bedrock Nova Micro |

Config: `infra/litellm/config.yaml` (image pinned in `docker-compose.litellm.yml`).

## Run locally

```bash
export LOCAL_UID=$(id -u) DYNAMODB_TABLE_NAME=<table> LITELLM_MASTER_KEY=<local-only value>
docker compose -f docker-compose.yml -f docker-compose.aws.yml -f docker-compose.litellm.yml up --build
```

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

Rollback: set `LLM_PROVIDER=bedrock` (or `fake` / `serve`) and restart `app` and `worker`; drop the
`docker-compose.litellm.yml` overlay. The GPU gateway and the direct `gateway_chat` replayer never use the proxy.

## Known limitations

- Direct-vs-proxy live comparison and the multi-turn / rollback smokes need real AWS and are manual.
- Budget enforcement for the proxy path (`DynamoDBBedrockBudget`) is not applied; spend controls are #170.

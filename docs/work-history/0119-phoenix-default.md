# 0119 — Phoenix as the default local trace UI (#190)

## Goal
Inspect every model call with its full prompt, response, tool-call arguments and timings in an existing observability tool (Arize Phoenix) fed by the LiteLLM proxy already in the stack, instead of building a review app.

## Starting point
Proxy spans went only to the Collector/Jaeger when `LITELLM_OTEL=1`; full bodies existed only in the proxy DB with `LITELLM_CAPTURE=1`. A throwaway spike showed Phoenix renders the proxy's spans with messages, tool calls, tokens and cost.

## Decisions
- `docker-compose.phoenix.yml` joins `LITELLM_COMPOSE`, so `make local-aws-litellm-compose` starts Phoenix by default (user decision).
- `infra/litellm/config.yaml` stays untouched: Terraform embeds it for the deployed proxy. `PHOENIX_*` env on the local proxy is enough for LiteLLM to attach its Phoenix logger (log line "Auto-initialized Arize Phoenix logger alongside otel").
- Image pinned to `arizephoenix/phoenix:version-20.26.1`; loopback-only port; named volume `phoenix_data`.
- Targets: `phoenix-up`, `phoenix-down`, `phoenix-ui`, `phoenix-purge`.
- Out of scope (follow-ups): `session.id` = conversation id, exporting app/MCP spans to Phoenix, per-conversation export/list.

## Verification
- Isolated stack from the worktree on ports 3300/14300/16306: `/api/status` ok, Phoenix 200, proxy log shows the Phoenix logger attached.
- Message capture without the spike's explicit `turn_off_message_logging` is verified by one live query (manual, see PR).

## PR / merge state
Branch `feat/190-phoenix-default`.

## Lessons
- Check what a shared config file is also used for (Terraform) before adding a local-only callback to it.

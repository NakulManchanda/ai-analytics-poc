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
- The app and MCP export their spans to Phoenix directly (overlay env); the app adds the OpenInference `session.id` (= conversation id) to the `ai.run` span, one line in `loop.py` with the test updated. LiteLLM itself only fills `session.id` from its end-user id, which we do not repurpose.
- `OTEL_RESOURCE_ATTRIBUTES`/`PHOENIX_PROJECT_NAME` did not route spans to a named project, so everything is in `default` and the extra env was dropped.
- Out of scope (follow-ups): tool result rows on MCP spans, per-conversation JSON export helper, status-poll noise traces.

## Verification
- Isolated stack from the worktree on ports 3300/14300/16306: `/api/status` ok, Phoenix 200, proxy log shows the Phoenix logger attached.
- Live two-turn conversation on the isolated stack: both runs are one Phoenix session (`conv_...`) and each is a single trace `ai.run` -> LLM calls -> `mcp.request` -> `mcp.tool.execute` -> `duckdb.query`; LLM spans with messages were present under default message logging.

## PR / merge state
Branch `feat/190-phoenix-default`.

## Lessons
- Check what a shared config file is also used for (Terraform) before adding a local-only callback to it.

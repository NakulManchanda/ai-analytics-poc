# 0127 — Phoenix as the single local trace UI; Jaeger opt-in (#221)

## Goal
One local trace viewer. Phoenix already receives app, MCP and LiteLLM spans (#190) and is the review UI; the observability stack still shipped Jaeger as a second viewer, with its own docs, ports and smoke check.

## Starting point
The default local path (`make local-aws-litellm-compose`, `make eval-stack-up`) was already Phoenix-only. Jaeger appeared only in the opt-in observability stack (`observability-up`, `observability-dev-up`, `LITELLM_OTEL=1`): the Collector's only trace exporter was Jaeger, Grafana had a Jaeger datasource, Prometheus probed it, and the smoke asserted traces in Jaeger. ADR 0007 named Jaeger the local trace UI.

## Decisions
- Collector -> Phoenix: `otlphttp/phoenix` (`http://phoenix:6006`, no compression) is the Collector's only trace exporter; Prometheus probes Phoenix's `/healthz` instead of Jaeger; the observability stack always layers `docker-compose.phoenix.yml` (`OBS_COMPOSE` in the Makefile).
- Jaeger is the opt-in overlay `docker-compose.jaeger.yml` (`JAEGER=1`; ports `JAEGER_UI_PORT`): the Jaeger service, an extra Collector config (`infra/observability/jaeger/`, merged with a second `--config`, exporting to Phoenix and Jaeger) and a Grafana Jaeger datasource. An overlay rather than a Compose profile because the Collector command, Grafana provisioning and an app env var also change.
- `docker-compose.phoenix.yml` could not be layered without LiteLLM because it also configured the `litellm` service (no image there). The proxy's Phoenix logger moved to `docker-compose.phoenix-litellm.yml`; `LITELLM_COMPOSE` layers both, so the default path is unchanged.
- `AI_TRACE_RUN_IO` (#219): on with the Phoenix overlay. In the default observability stack spans still end only in Phoenix (via the Collector), so it stays on. `docker-compose.jaeger.yml` forces it to `0` because spans then also leave for Jaeger. The smoke scripts set it to `0` to keep asserting the metadata-only default.
- `make observability-smoke` / `scripts/smoke/16_observability.sh` check Phoenix through its REST API (`/v1/projects/default/spans?name=ai.run`, `?trace_id=`): run id, attributes, span names, parent chain, and that no private text reached the trace. The previous Jaeger-API script is `16_observability_jaeger.sh` (`make observability-jaeger-smoke`), run against the overlay.
- Issue-scoped targets (`observability-dev-*`) gain `OBSERVABILITY_PHOENIX_PORT` (16106, off the default 16006); `observability-down` removes orphans so a Jaeger container is cleaned up.
- ADR 0007 amended (Phoenix primary, Jaeger optional, OTEL and the Collector unchanged); README, `docs/litellm-proxy.md` (the "Phoenix shows only the proxy's spans with `LITELLM_OTEL=1`" gap is gone), `docs/evals.md`, `docs/observability-stack.md` and the dev-commands and local-docker-test skills updated.
- Out of scope: deleting Jaeger configuration, Tempo or another backend, the deployed AWS tracing path, Prometheus/Grafana dashboards.

## Verification
- `uv run --project services/app pytest tests/scripts/test_observability_compose.py`: 8 passed (service lists, Collector exports, Jaeger overlay, split Phoenix files, overlay order in the Makefile, run-I/O flag per destination, `docker compose config` for the Phoenix and Jaeger combinations).
- `docker compose config` for the default observability stack, the Jaeger overlay and the LiteLLM path (`LITELLM_OTEL=1`) all validate.
- `./scripts/smoke/16_observability.sh` (isolated project, ephemeral ports): passed; the trace reached Phoenix through the Collector.
- `./scripts/smoke/16_observability_jaeger.sh`: see PR description for the result.
- Not verified: Grafana's Jaeger datasource in a browser and `LITELLM_OTEL=1 make local-aws-litellm-compose` end to end (needs AWS).

## PR / merge state
Branch `feat/221-phoenix-single-trace-ui`; draft PR.

## Lessons
- An overlay that touches a service defined in another overlay (`litellm`) is only valid with that overlay; keep each overlay self-contained.
- Phoenix's REST API (`/v1/projects/<project>/spans`, filters `name`, `trace_id`) is enough for a smoke check; attributes come back flat (`ai.run_id`).

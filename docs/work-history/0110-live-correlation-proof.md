# 0110 — live correlation proof (#189 PR-D)

## Goal
Show on the real stack that one `eval_run_id` selects the capture records, run events, LiteLLM spend rows and Jaeger traces of a batch, without an external manifest.

## Starting point
PR-A..C (#196-#198) added the `ai.run` span on the async path, the opt-in `eval_run_id`, and the LiteLLM correlation headers, all unit-tested only. The proxy's `otel` callback had no exporter env, so it fell back to its console exporter and no proxy span reached Jaeger.

## Decisions
- New env-only overlay `docker-compose.litellm-otel.yml` (`OTEL_EXPORTER=otlp_http`, `OTEL_ENDPOINT=http://otel-collector:4318`, `OTEL_SERVICE_NAME=litellm-proxy`). LiteLLM v1.106.0 normalizes the base endpoint to `/v1/traces`. It only works with `docker-compose.litellm.yml` and `docker-compose.observability.yml`, so it is a separate file rather than an edit to either.
- `LITELLM_OTEL=1` adds both overlays to the existing `LITELLM_COMPOSE` file list, so `local-aws-litellm-compose`, `local-litellm-compose`, the matching down target and the logs target all stay consistent. No new Make target.
- No `evals correlate` helper: the join is four filters on documented fields. It is documented in `docs/evals.md` ("Correlating an eval run").
- Per the #198 review, capture `call_id` -> spend rows is documented as 1:N. `(call_id, attempt)` identifies one physical request, and a failed attempt may emit no row.
- No app code changed.

## Verification
- Experiment: one proxy call with a fixed `traceparent` produced 9 `litellm-proxy` spans under that trace id in Jaeger.
- Live run (Nova Micro, total spend $0.00007): driver label and `eval_run_id` `2026-10-20-189d-correlation`, cases det-004 and det-007, both `run.completed`. A throwaway join script checked:
  - 2 `model_call` capture records, 2 `tool_call` records and 104 run events, all carrying the `eval_run_id`;
  - 2 tagged spend rows, each `attempt=1`, mapping both ways to the capture `call_id`s and carrying `run_id` and `eval_run_id`;
  - event `llm_call_id`s are a subset of the capture `call_id`s;
  - both capture `trace_id`s are non-null, and each Jaeger trace holds `ai-analytics-app` (1), `analytics-mcp` (8) and `litellm-proxy` (7) spans;
  - capture `cost_usd` equals proxy `spend` ($0.00007049).

## Limitations
- Both turns were served by the catalogue, so only the answer call path was exercised live. The planner proposal and proposal-retry call sites are covered by unit tests only (PR-C).
- No fallback occurred, so the 1:N case is documented but not observed live.
- Deployed ECS has no proxy OTEL exporter.

## PR / merge state
See the PR for #189 PR-D.

## Lessons
- Check what a library does with no configuration before assuming it does nothing. Here the `otel` callback was already active and printing spans to the console.

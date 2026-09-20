# 0057 — Local OpenTelemetry and Jaeger trace skeleton

## Goal

Deliver the first bounded implementation slice from ADR-0007: create one safe
OpenTelemetry trace for a synchronous AI run and make it inspectable in a local
Jaeger UI without changing the existing metrics, logs, or application behavior.

## Starting point

The application already emitted semantic Redis/SSE events, durable run metadata,
CloudWatch EMF-compatible run metrics, and local JSONL metrics. It did not have
an OTEL SDK/exporter, a distributed trace, a Collector, or a local trace backend.

Issue #116 authorized only the local root-trace skeleton. Cross-process worker,
Redis, MCP, DuckDB, Bedrock, Langfuse, AWS, and dashboard instrumentation remain
later slices.

## Decisions and changes

1. Added an explicit `TelemetrySettings` and isolated `TracingRuntime` rather
   than installing a process-global tracer provider.
2. Made tracing opt-in and fail-open. Missing or invalid exporter configuration
   cannot prevent application startup or fail a request.
3. Injected the tracer through `create_app` into `OrchestrationLoop`.
4. Wrapped synchronous `run()` execution in one `ai.run` span containing only
   bounded identifiers and operational dimensions: run, conversation, turn
   type, model, and terminal status.
5. Excluded prompts, responses, SQL, tool payloads, credentials, and secrets
   from span attributes.
6. Added a Compose-only OTEL Collector trace pipeline. The application exports
   OTLP/HTTP to the Collector; the Collector applies memory limiting and
   batching, then exports OTLP/gRPC to Jaeger.
7. Added Jaeger 2 all-in-one with loopback-only UI exposure and transient
   in-memory trace storage. Collector ingestion ports remain internal to the
   Compose network.
8. Added `observability-up`, `observability-down`, and `observability-smoke`
   Make targets.
9. Made the automated smoke own a unique Compose project and dynamic host ports.
   It submits a fake-model request, queries Jaeger's v3 API, validates the span,
   checks prompt exclusion, and always removes only its own services.
10. Preserved CloudWatch EMF and local JSONL as the aggregate metrics path; this
    change adds traces rather than replacing metrics.

The initially planned Collector `0.161.0` image was documented upstream before
its container manifest was available from the tested registries. The verified
official GHCR `0.157.0` image is pinned instead.

## Verification

- Focused Python telemetry, orchestration, application-factory, and API tests:
  24 passed.
- Full application suite: 140 passed.
- Full MCP suite: 1 passed.
- `docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet` passed.
- `make observability-smoke` passed and returned one matching `ai.run` trace
  from Jaeger.
- `make -n observability-up` and `make -n observability-down` rendered the
  intended two-file Compose commands.
- `git diff --check` passed.

## PR and issue state

- Issue: #116
- Draft PR: #117
- Branch: `codex/o1-local-otel-jaeger`
- State: implementation and full local verification complete; independent PR
  review remains before merge readiness.

## Limitations and next slice

- Only synchronous `/api/ask` creates the root span.
- Redis job envelopes do not yet carry W3C trace context.
- The worker and MCP process do not initialize OTEL.
- MCP, DuckDB, and Bedrock do not yet create child spans.
- Jaeger data is intentionally lost when its local container is removed.
- Langfuse, AWS ADOT/X-Ray, logs-to-OTEL, generic HTTP RED metrics, and
  Prometheus/Grafana are deferred.

The next trace slice should propagate context through Redis and MCP before
adding more backends or dashboards; without propagation, additional service
spans would form unrelated traces.

# Observability Layers

The POC has several different observability questions, and each deserves the right signal/backend rather than forcing everything into one system.

## 1. Semantic run events

Existing Redis/SSE lifecycle events answer:

> What logically happened during this AI run?

Examples include `run.received`, `llm.started`, `tool.started`, `context.reduced`, and terminal run events. These are domain-level events and power the Timeline Inspector.

## 2. Distributed traces

OpenTelemetry answers:

> How did this request execute technically across components and service boundaries?

A trace should show nested work, timing, errors, parent/child relationships, and propagation across independently deployable services.

## 3. Aggregate metrics

ADR 0007 CloudWatch EMF and local Streamlit/DuckDB tooling answer:

> How is the population of runs behaving over time?

This is where p50/p95 latency, TTFT, token use, model comparison, failure rates, and cost trends belong.

## 4. AI-native run inspection

Langfuse answers:

> What happened inside the model/tool/agent execution of this run?

It is the preferred view for individual LLM generations, ReAct iterations, tool calls, token/cost attribution, and future evaluation signals.

Langfuse should consume vendor-neutral OTEL/OTLP data where practical. It is a backend/view, not the instrumentation contract.

## Question → best place

| Question | Best place |
| --- | --- |
| Why is the API/MCP/service failing? | AWS / OTEL |
| Why is p95 latency rising across requests? | CloudWatch metrics |
| What happened inside this ReAct/agent run? | Langfuse |
| Which model/tool step was slow or expensive? | Langfuse |
| How many tokens/cost did this run or step consume? | Langfuse |
| Did answer quality regress? | Langfuse / evaluation layer |
| Is the service unhealthy? | AWS / operational monitoring |
| How did one request cross FastAPI → MCP → DuckDB? | OTEL distributed trace |

## Why not collapse these layers?

A semantic event such as `tool.completed` carries AI workflow meaning that a generic HTTP span does not. A trace is much better than a flat event list for reconstructing cross-service causality. Metrics are much cheaper and easier to aggregate than high-cardinality trace data. Langfuse is optimized for AI execution inspection and evaluation, not fleet health.

The intended relationship is:

```text
single-run AI semantics        -> Redis/SSE Timeline
single-request technical path  -> OTEL trace
single-run AI execution        -> Langfuse
cross-run/fleet behavior       -> CloudWatch / Streamlit
```

All layers should correlate through stable identifiers rather than replace one another.

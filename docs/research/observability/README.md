# Observability Research

This directory captures the observability model for the AI analytics POC.

The system intentionally keeps complementary signals and backends:

```text
Redis/SSE semantic events  -> what logically happened in this AI run
OpenTelemetry traces       -> how the request executed across stages/services
CloudWatch/Streamlit       -> how the population of runs behaves over time
Langfuse                    -> AI-native model/tool/agent inspection and future evals
```

OpenTelemetry is the instrumentation standard. Langfuse is an AI-observability backend/view; it does not replace OTEL or AWS operational monitoring.

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

## Research notes

- [01-observability-layers.md](01-observability-layers.md) — signal/backend responsibilities and the question-driven split.
- [02-distributed-trace-model.md](02-distributed-trace-model.md) — target FastAPI -> MCP -> DuckDB trace shape, correlation model, and minimum AI telemetry contract.
- [03-implementation-demo.md](03-implementation-demo.md) — OTEL Collector, Jaeger/AWS, Langfuse, and demo expectations.

## Related decisions and work

- ADR 0007: telemetry, CloudWatch metrics, OTEL, Langfuse, and local comparison architecture.
- Issue #111: OpenTelemetry distributed tracing across app orchestration and MCP execution.

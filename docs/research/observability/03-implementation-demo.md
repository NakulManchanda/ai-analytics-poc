# OTEL Implementation and Demo

Keep the first implementation deliberately small and demonstrable.

## Local / backend shape

```text
App + MCP
   │ OTLP
   ▼
OTEL Collector
   ├─ Jaeger / AWS  -> distributed-system and operational observability
   └─ Langfuse      -> AI model/tool/agent observability
```

Application code should use OpenTelemetry APIs and OTLP so backends can evolve without changing stage instrumentation.

## Reuse existing telemetry work

Do not reimplement ADR 0007 aggregate metrics through OTEL in this milestone. Reuse the same stage vocabulary and metadata where useful, while keeping CloudWatch EMF as the aggregate metrics path.

Existing measurements such as proposal LLM latency, tool latency, final-answer latency, TTFT, token counts, model, milestone, strategy/turn type, step index, tool name, estimated cost, and status/error can appear as safe span attributes/events where they explain one request.

## Demo flow

For one `/api/ask` request, demonstrate complementary views:

1. Timeline Inspector — semantic AI lifecycle events for the run.
2. OTEL distributed trace — one request spanning FastAPI, MCP, and DuckDB/tool execution.
3. Langfuse — AI-native hierarchy for model calls, ReAct iterations, tool calls, tokens, latency, and cost.
4. CloudWatch or local Streamlit — aggregate latency/cost behavior across runs.

The strongest propagation assertion is:

```text
FastAPI orchestration span
        ↓
MCP server span
        ↓
DuckDB/tool span

all share the same trace_id.
```

For ReAct/agent runs, Langfuse should make the execution hierarchy easy to inspect, for example:

```text
ai.run
├─ classify -> react
├─ iteration 1
│  ├─ llm
│  └─ tool
├─ iteration 2
│  ├─ llm
│  └─ tool
└─ final generation
```

## Implementation checks

- tracing can be disabled without changing request behavior;
- errors are recorded on the relevant span;
- `run_id` can be mapped to `trace_id`;
- app -> MCP propagation preserves the same trace;
- AI/model/tool spans carry the minimum telemetry contract where applicable;
- Langfuse receives inspectable AI traces without requiring proprietary instrumentation in core orchestration code;
- no raw prompt is exported by default;
- continued request traffic with missing expected traces or missing MCP child spans is treated as an observability failure.

See Issue #111 for implementation acceptance criteria.

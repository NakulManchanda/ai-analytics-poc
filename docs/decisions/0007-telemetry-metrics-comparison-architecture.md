# ADR 0007 — Telemetry, CloudWatch Metrics, and Local Comparison Architecture

## Status

Accepted.

## Context

As the platform transitions from text-only analytics (v1–v3.1) into realtime voice input, speech synthesis, barge-in, and multimodal comparisons (v4–v8), evaluating and improving performance requires granular telemetry.

Runs persist point-in-time telemetry in DynamoDB under `RUN#<run_id>` / `METADATA`, but cross-run analysis needs a lower-friction aggregate path. At the same time, distributed AI execution now spans application orchestration, MCP/tool execution, and model calls, which requires trace-level correlation in addition to metrics.

We need a unified observability model that:
1. Keeps the live analytical loop low-overhead.
2. Preserves CloudWatch EMF for aggregate/fleet metrics.
3. Adds vendor-neutral OpenTelemetry tracing across app and MCP boundaries.
4. Adds an AI-native trace backend for model/tool/agent inspection.
5. Keeps semantic run events, distributed traces, and aggregate metrics distinct but correlated.

---

## Decisions

### 1. CloudWatch EMF remains the aggregate metrics path

Production emits structured EMF JSON to `stdout`; local development may append compatible run summaries to `metrics/runs.jsonl` for DuckDB/Streamlit comparison.

CloudWatch answers population-level questions such as p50/p95 latency, error rate, TTFT, token usage, cost trends, and model/milestone comparison.

### 2. DynamoDB remains durable run metadata

A GSI may be used for efficient cross-run lookup without table scans. DynamoDB is not the tracing backend.

### 3. Streamlit + DuckDB remains the local comparison workflow

Developer benchmarking stays outside the production React UI. Local JSONL exports can be queried directly with DuckDB and visualized with Streamlit.

### 4. OpenTelemetry is the instrumentation and distributed-tracing standard

OpenTelemetry is additive to the metrics path, not a replacement for CloudWatch EMF.

```text
Redis/SSE semantic events  -> what logically happened in this AI run
OpenTelemetry traces       -> how the request executed across stages/services
CloudWatch/Streamlit       -> how the population of runs behaves over time
```

Application and MCP services should propagate standard W3C trace context so one request remains reconstructable across service boundaries.

Representative trace:

```text
FastAPI
└─ ai.run
   ├─ conversation.resolve
   ├─ context.schema.load
   ├─ llm.plan
   ├─ mcp.tool.execute
   │   └─ HTTP MCP server
   │       └─ duckdb.query
   ├─ context.reduce
   └─ llm.generate
```

### 5. Langfuse is the AI-observability backend, not the instrumentation contract

Keep application instrumentation vendor-neutral through OTEL/OTLP. Export AI-relevant traces to Langfuse so model, tool, agent/ReAct, token, cost, and future evaluation data can be inspected in an AI-native view.

AWS/CloudWatch remains the operational and fleet observability path.

```text
App / MCP
   ↓
OpenTelemetry
   ├─ AWS / Jaeger  -> operational + distributed-system observability
   └─ Langfuse      -> AI run/model/tool observability and future evals
```

Do not couple core orchestration code directly to Langfuse-specific APIs when OTEL can carry the same trace model.

### 6. Minimum AI telemetry contract

Major AI/model/tool spans should use a consistent safe vocabulary, using OpenTelemetry GenAI semantic conventions where available:

```text
run_id
conversation_id
model
strategy / turn_type
step_index
input_tokens
output_tokens
duration_ms
estimated_cost
tool_name (when applicable)
status / error
```

Do not attach raw prompts, unrestricted user content, secrets, or raw SQL to span attributes by default.

---

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

---

## Existing metric payloads

Existing EMF/JSONL payloads remain valid. Continue emitting run-level fields such as:

```text
run_id
conversation_id
milestone
model
turn_type
status
end_to_end_latency
proposal_llm_latency
tool_latency
final_answer_llm_latency
TTFT
input_tokens
output_tokens
estimated_cost
started_at
completed_at
```

These aggregate fields should reuse the same naming/vocabulary as traces where practical, without forcing all telemetry into one backend.

---

## Metrics Question Guide

### Voice latency
- end-of-speech → STT final
- tool execution
- LLM TTFT
- TTS time-to-first-audio
- total turn latency

### Barge-in and cancellation
- time from detected speech to audio halt
- LLM cancellation latency
- avoided tokens/audio after interruption

### Model comparison
- p50/p90/p95 latency
- TTFT
- token usage
- estimated cost
- quality/evaluation signals

### Cascaded voice vs native speech-to-speech
- conversational latency
- tool-call reliability
- observability/auditability
- cost per conversational minute

---

## Consequences

- **Separation of signals:** semantic lifecycle events, distributed traces, AI-native traces, and aggregate metrics remain distinct but correlated through stable IDs.
- **Vendor-neutral instrumentation:** OTEL/OTLP is the application contract; backends can change independently.
- **AI-native debugging:** Langfuse becomes the preferred place to inspect individual model/tool/agent executions.
- **Operational authority:** AWS/CloudWatch remains the source for service health and fleet-level metrics.
- **Frontend decoupling:** the production React app does not become the developer observability console.

See `docs/research/observability/` and Issue #111 for the detailed tracing model and implementation plan.

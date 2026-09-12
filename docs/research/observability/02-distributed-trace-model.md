# Distributed Trace Model

The OTEL goal is one reconstructable trace across independently deployable services.

```text
trace_id = 8af34...

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

The important property is propagation, not merely span creation. The FastAPI app must send standard W3C trace context to the MCP service, and the MCP service must continue the incoming trace rather than create an unrelated root trace.

## Correlation

Carry these identifiers together where practical:

```text
run_id
conversation_id
trace_id
```

The OTEL root span can expose safe structured attributes such as:

```text
ai.run_id
ai.conversation_id
ai.milestone
ai.turn_type
gen_ai.request.model
```

Existing semantic lifecycle events may eventually include `trace_id` so the Timeline Inspector can link a run to its distributed trace.

## Minimum AI telemetry contract

Every major AI/model/tool span should emit a consistent safe set of attributes, using OpenTelemetry GenAI semantic conventions where available:

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

This contract should be reused across app orchestration, MCP/tool execution, Langfuse, and aggregate metrics where the field is meaningful.

## Span boundaries

Prefer explicit manual spans for meaningful AI stages:

```text
ai.run
├─ conversation.resolve
├─ context.schema.load
├─ llm.plan
├─ mcp.tool.execute
├─ context.reduce
└─ llm.generate
```

Inside MCP:

```text
mcp.tool.execute
└─ duckdb.query
```

Automatic FastAPI and HTTP client/server instrumentation is still useful for transport timing and propagation, but should complement rather than replace semantic spans.

## Backends

OTEL/OTLP is the instrumentation contract. The same trace model may be exported to multiple backends:

```text
OpenTelemetry
├─ Jaeger / AWS  -> distributed-system and operational debugging
└─ Langfuse      -> AI model/tool/agent inspection and future evals
```

Do not make core application behavior depend on one observability backend.

## Data safety

Do not attach raw prompts, raw SQL, secrets, or unrestricted user content to span attributes by default. Prefer identifiers, counts, model/tool names, status, and bounded metadata.

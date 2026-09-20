# AI Analytics Observability Stack

| Layer | Tool | What to emit |
| --- | --- | --- |
| **Tracing**<br>Every run | OpenTelemetry<br>OTEL Collector<br>Jaeger<br>Langfuse | Spans for `ai.run`, `llm.plan`, `mcp.tool`, and `duckdb.query`<br>Duration, tokens, model, `run_id`, `trace_id`, and status |
| **Metrics**<br>Operations | CloudWatch EMF<br>JSONL + Streamlit | p50/p95/p99 latency<br>TTFT, input/output tokens, cost per run, and success/error rate |
| **Events**<br>Product timeline | Redis + SSE<br>Timeline Inspector | Run received/completed<br>Tool started/completed, answer deltas, and cancellation |
| **Quality**<br>Evaluation signals | Langfuse evaluations | Sampled faithfulness, answer relevance, tool correctness, and hallucination flag |
| **Logs**<br>Debugging | Structured stdout<br>CloudWatch Logs | `run_id`, `trace_id`, component, error code, retry, and failure details |
| **Dashboards**<br>Visibility | CloudWatch<br>Streamlit<br>Grafana (Day 2) | Latency by AI stage, token/cost trends, failure taxonomy, and voice/realtime latency |
| **Alerting**<br>Incidents | CloudWatch Alarms<br>SNS | p95 latency increase, error-rate increase, queue delay, and cost-budget threshold |

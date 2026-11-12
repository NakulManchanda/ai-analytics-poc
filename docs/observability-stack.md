# AI Platform Observability

The application and inference system use complementary telemetry: **traces for execution flow**, **metrics for operational capacity**, **events for user-visible progress**, and **captured evidence for evaluation**.

| Signal | Implementation | Purpose |
| --- | --- | --- |
| **Distributed tracing** | OpenTelemetry → Collector / Phoenix | Correlate agent, LiteLLM, MCP, and tool operations by run/trace ID |
| **Operational metrics** | Prometheus / Grafana; GPU DCGM exporter | TTFT, latency, queues, admission decisions, GPU/KV pressure |
| **Product events** | Redis + SSE | Live/replayed run progress, tool steps, answer deltas and cancellation |
| **Structured logs** | Service logs, CloudWatch/Loki where configured | Error codes, request IDs, and operational diagnosis |
| **Quality evidence** | Capture records + golden graders + human review | Connect answer/tool verdicts to model calls and Phoenix traces |

The **human review workbench links a frozen evaluation run to its Phoenix trace**; grading and observability are complementary, not interchangeable. Some collectors and dashboards are environment-specific—an item in this architecture does not imply every backend runs in every deployment.

**References:** [Evaluations](evals.md) · [Capture format](capture.md) · [Inference dashboards](inference-experiments/README.md).

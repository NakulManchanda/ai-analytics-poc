# Experimental AI Platform Engineering

An open-source **NYC Taxi analytics application** used to explore agent orchestration, tool execution, model selection, observability, evaluation, and GPU inference. The goal is to compare engineering approaches using repeatable workloads—not just assemble frameworks.

![Experimental AI platform: circular map of areas explored](docs/images/experimental-platform-circular-stack.svg)

## What each area does

| Area | Purpose |
| --- | --- |
| **Application** | React/FastAPI analytics copilot, multi-turn conversations, and execution timeline. |
| **Agent harness** | Own planning, bounded ReAct/tool loops, context reduction, cancellation, and durable progress. |
| **MCP & data** | Execute validated, read-only DuckDB tools over NYC Taxi Parquet data. |
| **Model access** | Route model roles through LiteLLM/Bedrock; account for tokens, cost, and budgets. |
| **Inference gateway** | Control guardrails, tenant quotas, admission, placement, queuing, and overflow. |
| **GPU serving** | Serve Qwen with vLLM, prefix caching, and KV transfer. |
| **GPU infrastructure** | Explore A100 capacity and isolation with K3s, HAMi, and DCGM. |
| **Observability** | Correlate OpenTelemetry/Phoenix agent traces with Grafana, Prometheus, and Loki. |
| **Evaluations** | Replay golden cases and grade correctness, tool behavior, faithfulness, and cost. |
| **Cloud & state** | Preserve conversations and run state with DynamoDB/Redis; deploy using AWS/Terraform. |

## Experiments and benchmarks

| Area | Experiment / benchmark |
| --- | --- |
| **Inference bottlenecks** | Offered-load sweep: **KV capacity vs decode slots vs queueing**, using goodput, TTFT, p99, and GPU metrics. |
| **Warmup and caching** | Cold vs warm worker; first-pass vs reused-prefix TTFT. |
| **Gateway placement** | **Least-loaded vs prefix-aware routing** on identical multi-turn traces. |
| **Gateway admission** | **Admission on vs off** under overload: interactive goodput, tail latency, fairness, and sheds. |
| **KV locality** | Local reuse vs recompute vs **LMCache/Mooncake KV migration**; compare transfer cost and TTFT. |
| **Model economics** | Planner/answer model roles, **Nova Pro vs Micro** trade-offs, tokens and per-run cost controls; a full controlled quality benchmark remains future work. |
| **Agent reliability** | Bounded ReAct, multi-turn state, cancellation/replay, tool validation, and failure handling. |
| **Evals & tracing** | Versioned **37-case golden dataset**, deterministic/recorded-judge regression checks, capture/replay, and Phoenix trace inspection. **Golden cases are pending human review; recorded judge fixtures are synthetic.** |

## Next experiments

- **Agent harness:** custom ReAct **vs Deep Agents/LangGraph** on the same tasks, tools, models, budgets, and latency/cost measures. *CrewAI was not directly benchmarked.*
- **Planner models:** larger capable planner **vs smaller cheaper planner**, measuring valid tool plans, final accuracy, latency, and cost per correct answer.
- **Human evaluation:** separately rate **plan, tool action, and final answer**; calibrate model judges against reviewers.
- **Self-hosted inference:** compare owned vLLM serving against managed model endpoints for quality, throughput, isolation, and cost.
- **Security and recovery:** adversarial MCP tests, authorization boundaries, approvals, and crash/restart replay.

**Implementation and evidence:** [Inference experiments](docs/inference-experiments/inference-experiments-reference.md) · [GPU evidence](docs/inference-experiments/evidence/notebook/) · [Evals and limitations](docs/evals.md) · [Model policy](docs/model-policy.md) · [Architecture](DESIGN.md) · [Local setup](docs/local-uat-guide.md) · [Deployment](docs/demo-runtime.md)


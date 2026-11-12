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

## Tools we built

| Tool | Why it exists |
| --- | --- |
| **[Human review workbench](tooling/evals/src/evals/review_server.py)** | Review frozen runs, save **tool / answer / follow-up resolution** verdicts, compare humans with graders, and open the related **Phoenix trace**. Plan scoring is a future addition. |
| **[Evaluation harness](tooling/evals/)** | Replay captured runs against a 37-case golden dataset; grade correctness, grounding, tool use, cost, and baseline-vs-candidate regressions. Golden cases still await human approval. |
| **[Inference gateway](services/gateway/)** | Policy-owned guardrails, tenant quotas, admission/shedding, prefix-aware placement, queues, and KV-hop/overflow. |
| **[Trace and evidence pipeline](docs/observability-stack.md)** | Correlate app, MCP, and LiteLLM traces in **Phoenix**; link human reviews back to a run's trace. |
| **[Workload benchmark runner](tooling/benchmarks/)** | Replay fixed app and gateway scenarios; collect goodput, TTFT, queue and routing metrics for repeatable comparisons. |
| **[Model policy engine](services/app/app/model_policy/)** | Assign planner/answer model roles, enforce tenant entitlements and token/spend budgets, and track priced vs unknown usage. |

## Experiments and benchmarks

| Theme | Comparison |
| --- | --- |
| **Capacity and warmup** | GPU limits: KV memory vs decode slots vs queue; cold vs warm worker. |
| **Caching and placement** | First-pass vs reused-prefix TTFT; least-loaded vs prefix-aware placement. |
| **Admission and KV mobility** | Admission on/off under overload; local KV reuse vs recompute vs LMCache/Mooncake hop. |
| **Agent reliability and models** | Bounded multi-turn ReAct, cancellation and replay; Nova Pro planner vs Micro cost and tool-plan behavior. |
| **Quality and observability** | Golden-case and recorded-judge regression checks; human vs grader disagreements linked to Phoenix traces. |

## Next experiments

**Agent harness comparison:** custom ReAct vs opt-in **Deep Agents/LangGraph** using equivalent tasks, tools, and budgets (implementation exists; controlled benchmark still needed). **Planner economics:** capable vs smaller models on plan quality and cost per correct answer. **Human grading:** add plan ratings alongside implemented tool/answer reviews. **Self-hosted inference:** compare vLLM with managed endpoints. **Security:** adversarial MCP access and approval/recovery tests.

**Explore the evidence:** [Inference benchmark methodology](docs/inference-experiments/inference-experiments-reference.md) · [GPU results](docs/inference-experiments/evidence/notebook/) · [Evaluation limitations](docs/evals.md) · [Model policies](docs/model-policy.md) · [Architecture](DESIGN.md) · [Run locally](docs/local-uat-guide.md)


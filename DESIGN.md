# AI Inference Platform Architecture

The NYC Taxi analytics agent uses an **owned inference gateway** to serve self-hosted models, enforce request policies, and study GPU scheduling. The application remains responsible for orchestration and MCP tool execution; the gateway manages **where and when** model calls run.

## Request flow

```text
React / FastAPI → Agent → MCP / DuckDB (SQL evidence)
                    │
                 Model call
                    ↓
              Inference gateway
 Guard → Tenant quota → Admission → Placement → Queue → KV decision
                    ↓
             vLLM / Qwen workers
                    ↓
        Answer + traces + metrics
```

## Components

| Component | Responsibility |
| --- | --- |
| **Application** | Bounded agent/tool loop, conversations, grounding, model-call submission. |
| **Gateway** | Guard input/window size; enforce tenant limits; admit or shed; place requests; queue by worker; handle overflow. |
| **Serving** | vLLM executes prefill/decode and maintains paged KV cache. |
| **Infrastructure** | K3s on a single A100, with two HAMi-isolated Qwen3-0.6B workers; DCGM, Prometheus, Grafana. |
| **KV mobility** | Prefix-aware placement and optional LMCache/Mooncake reuse across workers, subject to compatibility/evidence checks. |

The gateway does **not** load model weights or execute inference. See [gateway service](services/gateway/README.md), [prefix contract](docs/prefix-contract.md), and [model access](docs/litellm-proxy.md).

## Important design choices

- **Admission precedes queuing:** reject requests beyond budgets, capacity, or viable deadlines before they consume GPU work.
- **Worker-local queues:** keep scheduling decisions visible in the gateway; vLLM handles admitted execution.
- **Prefix affinity:** route recurring conversation prefixes toward workers likely to retain matching KV blocks, trading locality against load.
- **Observable decisions:** emit queue wait, admission/shed reasons, TTFT, decode activity, KV pressure, and GPU resource metrics.
- **Explicit evidence:** successful KV transfer requires transfer and consumption evidence; lower latency alone does not prove a hop.

## What measurements established

The A100 experiments showed that **scheduler slots—not KV memory—limited the small-model setup**. Reused prefixes reduced first-turn TTFT in a controlled comparison, while prefix-aware placement improved some follow-up turns but was not a universal p95 improvement. Admission on/off was largely neutral in the measured conditions. These are **workload-specific observations**, not general claims about GPU serving.

A live, validated KV-hop benefit and larger-model generalization are **not yet demonstrated**. Measurements collected through an SSH tunnel cannot establish the proposed 100 ms end-user SLO.

**Evidence:** [Experiment definitions](docs/inference-experiments/inference-experiments-reference.md) · [Results and evidence](docs/inference-experiments/inference-evidence-index.md) · [Reproduction runbook](docs/inference-experiments/inference-run-playbook.md).

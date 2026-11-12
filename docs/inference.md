# Inference Platform Architecture

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

The gateway does **not** load model weights or execute inference. See [gateway service](../services/gateway/README.md), [prefix contract](prefix-contract.md), and [model access](litellm-proxy.md).

## Serving topology and startup configuration

The inference lab runs **K3s + HAMi on one NVIDIA A100**, with two colocated vLLM workers sharing the GPU. The workers are separate scheduling targets, **not two physical GPUs**.

| Layer | Configured settings | Why it matters |
| --- | --- | --- |
| **K3s / HAMi** | K3s NVIDIA runtime; `hami-scheduler`; two workers, each requesting 50% GPU memory and 50% GPU cores | Isolates worker resource shares for routing experiments; does not double physical GPU compute |
| **Model / engine** | vLLM `v0.11.0`; pinned `Qwen/Qwen3-0.6B`; `bfloat16` | Reproducible, small-model serving baseline |
| **Context / batching** | `--max-model-len 8192`; `--max-num-seqs 8`; `--max-num-batched-tokens 2048` | Caps context and concurrent sequences; gateway dispatch slots match the 8-sequence worker limit |
| **KV / prefix cache** | `--block-size 16`; `--kv-cache-dtype auto`; `--enable-prefix-caching`; `--gpu-memory-utilization 0.45` | Paged KV allocation and reuse of identical token prefixes within each worker |
| **Tool calls** | `--enable-auto-tool-choice`; `--tool-call-parser hermes` | Enables model-emitted structured tool calls |
| **Startup** | `/health` startup probe: 10-second interval × 30 attempts; readiness and liveness follow | Avoids restarting vLLM during weight loading / CUDA initialization |

Both [worker manifests](../infra/inference/k8s/workers/worker-a.yaml) and [HAMi bootstrap](../infra/inference/scripts/bootstrap.sh) define these settings.

### Two-stage worker readiness

**Kubernetes startup probe ≠ inference warmup.** The vLLM `/health` startup probe allows up to **300 seconds** (30 checks × 10 seconds) for model weights, CUDA initialization, compilation and CUDA graph setup without premature liveness restarts. Kubernetes readiness then confirms the HTTP server is healthy; it does **not** prove an inference request can finish promptly.

The gateway's separate **warm gate** (`WARM_GATE=1`) waits for **3 consecutive healthy worker metric scrapes**, then sends a real **one-token completion** directly to that worker's `/v1/chat/completions` endpoint (`Qwen/Qwen3-0.6B`, user message `ping`, `max_tokens=1`, `temperature=0`; 30-second request timeout). Only a successful response from the **same healthy epoch** marks the worker warm and eligible for normal placement. That probe pays the first-inference cost before user traffic.

When an already-warm worker becomes unhealthy and returns, gateway traffic ramps back in at **2 then 4 concurrent slots**, increasing every **30 seconds** only while queue and KV headroom permit. This is a *serving readiness policy*, not a guarantee of a particular TTFT or a full KV cache.

## Gateway routing and KV mobility

| Setting | Current configuration |
| --- | --- |
| **Placement** | `PLACEMENT_POLICY=prefix_then_load`; conversation-owner preference with `PLACEMENT_SPILL_QUEUE=4` |
| **Capacity** | `MAX_DECODE_SLOTS=8`, `WORKER_MAX_INFLIGHT=8` per worker; `BATCH_SLOT_RESERVE=0.25` |
| **Admission** | `MAX_MODEL_LEN=8192`; `KV_FREE_MIN=0.10`; tenant concurrency limit 10 |
| **Warm workers** | 3 healthy scrapes + warm probe; return-to-service ramp of 2 then 4 slots |
| **Optional overflow** | Disabled by default (`OVERFLOW_ENABLED=0`) |

**Three separate mechanisms:**
1. **Gateway prefix routing** uses `x-prefix-id` and worker/load observations to favor an existing KV owner, then spills when needed.
2. **vLLM prefix caching** reuses matching token blocks **within** a worker. A placement decision is not proof of a cache hit.
3. **LMCache + Mooncake** is an **opt-in experimental** cross-worker transfer path (TCP/CPU staging). Its rendered worker config adds `MooncakeKVConnector` via `--kv-transfer-config`, and the gateway must enable `KV_HOP_ENABLED=1`. Compatible model, tokenizer, prefix contract and cache namespace are required. **Live transfer benefit remains unproven**.

Configuration sources: [gateway manifest](../infra/inference/k8s/gateway/gateway.yaml) · [placement policy](../services/gateway/placement.py) · [Mooncake bundle](../infra/inference/mooncake/render.py) · [prefix contract](prefix-contract.md).

## Important design choices

- **Admission precedes queuing:** reject requests beyond budgets, capacity, or viable deadlines before they consume GPU work.
- **Worker-local queues:** keep scheduling decisions visible in the gateway; vLLM handles admitted execution.
- **Prefix affinity:** route recurring conversation prefixes toward workers likely to retain matching KV blocks, trading locality against load.
- **Observable decisions:** emit queue wait, admission/shed reasons, TTFT, decode activity, KV pressure, and GPU resource metrics.
- **Explicit evidence:** successful KV transfer requires transfer and consumption evidence; lower latency alone does not prove a hop.

## What measurements established

The A100 experiments showed that **scheduler slots—not KV memory—limited the small-model setup**. Reused prefixes reduced first-turn TTFT in a controlled comparison, while prefix-aware placement improved some follow-up turns but was not a universal p95 improvement. Admission on/off was largely neutral in the measured conditions. These are **workload-specific observations**, not general claims about GPU serving.

A live, validated KV-hop benefit and larger-model generalization are **not yet demonstrated**. Measurements collected through an SSH tunnel cannot establish the proposed 100 ms end-user SLO.

**Evidence:** [Experiment definitions](inference-experiments/inference-experiments-reference.md) · [Results and evidence](inference-experiments/inference-evidence-index.md) · [Reproduction runbook](inference-experiments/inference-run-playbook.md).

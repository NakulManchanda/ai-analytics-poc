# ADR 0010 — Package the Lambda inference lab as one remote bundle

## Status

Accepted

## Context

The inference track adds an owned vLLM cluster on Lambda without moving the existing FastAPI product, MCP/DuckDB service, web UI, or durable state. It must support controlled measurements of worker capacity, routing, prefix/KV locality, and overflow.

The class9b lab supplies useful mechanics—Lambda sync, SSH forwarding, k3s, HAMi, worker services, and GPU observability—but not the final experimental topology. Its workers serve different capabilities, and its Mooncake-named component records metadata rather than proving transfer of real KV tensors.

This Lambda/k3s environment is an explicitly requested, isolated course lab. It does not replace or extend the application's AWS-only reference deployment.

## Decision

Keep the product and canonical evidence local. Transfer only a self-contained `infra/inference/` subtree to Lambda.

```text
LOCAL
services/app/                  FastAPI agent and LLMClient
services/mcp/                  FastMCP and DuckDB
web/                           product UI
observability/                 existing product observability
metrics/inference/             pulled experiment evidence
experiments/                   local analysis/notebooks, when added

TRANSFERRED TO AND EXECUTED ON LAMBDA
infra/inference/
  README.md
  gateway/                     added by #121, policies added by #122
  k8s/
    hami/
    workers/
    services/
    lmcache/                   only when real integration begins
    mooncake/                  only when real integration begins
  observability/
    prometheus/
    grafana/
    dcgm/
  experiments/                runners execute near the workers
  scripts/                     sync, tunnel, deploy, smoke, pull, teardown
```

All source remains local and versioned. “Remote” means copied to and executed on Lambda. Synchronization excludes `.env`, credentials, caches, local metrics, product services, web assets, and durable state.

### Runtime ownership

```text
Local FastAPI application
  owns the agent loop, state, budgets, tools, and each LLM-call decision
        |
        | SSH tunnel initially
        v
Lambda gateway
  owns guard -> admit -> place -> queue -> overflow -> proxy
        |
        +----------------------+----------------------+
        v                                             v
same-model vLLM worker A                    same-model vLLM worker B
```

The gateway and controlled load generator run beside vLLM so WAN/SSH latency does not contaminate worker snapshots, queueing, or TTFT measurements. Prometheus, Grafana, and DCGM also run remotely; Grafana is viewed through a tunnel. Run manifests and scrapes are pulled into `metrics/inference/<run-id>/`.

### Provider boundary

Retain the existing application-owned abstraction:

```text
LLMClient
  |-- Bedrock adapter
  `-- OpenAI-compatible adapter
        |-- Lambda gateway -> vLLM
        `-- Superlinked/SIE overflow
```

Do not introduce LiteLLM Proxy in the initial track. Its routing, fallback, budgets, and metrics overlap the mechanisms being implemented and measured. Bedrock remains explicitly selectable. Superlinked/SIE is the single planned automatic overflow destination so results and costs remain attributable.

Only explicit capacity outcomes such as `503` or `529` may overflow. `429`, application `500`, slice OOM, and configuration failures remain local. The adapter belongs to #121 and the real overflow gate to #122; #120 only preserves their network contract.

### Worker and HAMi contract

Start with two replicas of the same text model using the same revision, weight/KV dtypes, context and batching limits, prefix-cache settings, and nominal GPU allocation. Different models would confound least-loaded versus prefix-aware routing.

On one physical GPU, evaluate two observable 50/50 HAMi-style slices when feasible. Verify memory visible inside each pod before setting vLLM's `--gpu-memory-utilization`; the two percentages must not be assumed to share a denominator. Move Worker B to another GPU if duplicated weights or same-GPU contention makes the experiment uninterpretable, and record why.

### SSH boundary

Use configurable local SSH forwards for the initial lab; do not expose raw vLLM ports publicly.

```text
local 18001 -> Lambda 127.0.0.1:8001   worker A smoke
local 18002 -> Lambda 127.0.0.1:8002   worker B smoke
local 18080 -> Lambda 127.0.0.1:8080   gateway after #121
local 13000 -> Lambda 127.0.0.1:3000   Grafana
```

Public HTTPS, VPN, mTLS, and production identity are separate future decisions.

## KV-capacity method

Calculate a paper baseline and reconcile it with vLLM startup output and live measurements.

```text
KV bytes/token =
  2 (key + value)
  x num_attention_layers
  x num_key_value_heads
  x head_dim
  x bytes_per_KV_element

KV budget/worker =
  GPU memory visible to worker
  - loaded weights
  - CUDA graphs, activations, allocator/runtime reserve
  - safety headroom

paper sequence ceiling =
  floor(KV budget / (KV bytes/token x assumed tokens/sequence))
```

The calculation must name its assumptions. Tensor parallelism, hybrid/sliding-window attention, KV quantization, block rounding, fragmentation, and prefix sharing can change per-device or effective capacity. A single-GPU replica loads a full model copy.

For each worker record:

1. GPU identity, physical HBM, HAMi allocation, and memory visible inside the pod.
2. Exact model/revision, weight and KV dtypes, and attention dimensions from model config.
3. vLLM context, batching, prefix-cache, block-size, and memory-utilization flags.
4. HBM before and after initialization plus vLLM-reported KV tokens/blocks.
5. Taxi context lengths using the same tokenizer: p50, p95, and configured maximum.
6. Paper ceilings at those lengths and the measured concurrency, TTFT, and goodput limit.

Do not set `max_num_seqs` from the KV ceiling alone. Prefill/decode compute, scheduler behavior, TTFT/deadline targets, HAMi contention, and preemption may limit useful concurrency first.

## Issue boundaries

- **#120:** remote cluster, two workers, capacity/warmup, observability, lifecycle, evidence pullback.
- **#121:** thin remote serve path and OpenAI-compatible application adapter.
- **Minimal #115:** deterministic and ReAct workload shapes.
- **#122:** admission, placement, gateway queues, prefix tracking, and overflow.
- **#123:** controlled A/B experiments and final proof.

The folder tree is the end-state boundary, not permission for #120 to pre-build later issues.

## Alternatives considered

- **Transfer the entire repository:** rejected because it copies unrelated product code and weakens the deployment contract.
- **Keep worker control local:** rejected because WAN and tunnel latency would contaminate control decisions and measurements.
- **Deploy LiteLLM Proxy:** deferred because it duplicates the behavior under study; it can be reconsidered behind `LLMClient` if provider diversity later warrants it.
- **Run controlled load locally:** rejected for serving measurements; local traffic remains acceptable for end-to-end smoke tests.
- **Copy class9b wholesale:** rejected because its capability split and metadata-only hop demonstration do not satisfy this experiment.

## Consequences

- One directory can recreate and remove the remote lab.
- Local product ownership remains unchanged.
- Cluster decisions and measurements occur beside the engines.
- Exact model, GPU, HAMi visibility, vLLM capacity, and first limiter remain measured outputs of #120.
- Two workers on one GPU may be rejected after measurement if duplicated weights or contention dominate.

## References

- [Inference project plan](../inference-project-plan.md)
- [Lambda Cloud firewalls](https://docs.lambda.ai/public-cloud/firewalls/)
- [Lambda Cloud SSH tunnelling](https://docs.lambda.ai/public-cloud/on-demand/connecting-instance/)
- [HAMi GPU virtualization](https://project-hami.io/docs/core-concepts/gpu-virtualization)
- [vLLM serving](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [vLLM metrics](https://docs.vllm.ai/en/latest/usage/metrics/)
- [Superlinked Inference Engine](https://github.com/superlinked/sie)

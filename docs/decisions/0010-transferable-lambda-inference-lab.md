# ADR 0010 — Package the Lambda inference lab as a self-contained remote bundle

## Status

Accepted

## Context

The application currently runs its FastAPI orchestration and FastMCP analytics services locally and calls Amazon Bedrock remotely. The inference project replaces the primary model path with an owned vLLM cluster on a Lambda GPU instance so that admission, placement, queueing, prefix/KV locality, and overflow decisions can be measured.

The existing class9b lab proves a useful deployment pattern: sync a small bundle to Lambda, run k3s and HAMi on the GPU host, keep cluster services off the public Internet, and reach them through SSH port forwarding. It is reference material rather than a deployable dependency. In particular, its two workers are split by text/vision capability rather than being identical replicas for a controlled routing experiment, and its Mooncake-named service records hop metadata rather than demonstrating transfer of real KV tensors.

This is an explicitly requested, isolated inference-course lab. It does not replace or extend the application's AWS-only reference deployment: the product, MCP service, web UI, durable state, and existing AWS infrastructure remain unchanged.

The repository therefore needs a boundary that is:

- small enough to copy to a short-lived Lambda instance;
- complete enough to start, observe, exercise, and tear down the remote lab;
- separate from the local product and its durable state;
- explicit about which measurements are predictions and which are observed;
- compatible with the issue order: #120 cluster, #121 serve path, minimal #115 workload split, #122 control plane, then #123 experiments and proof.

## Decision

Keep the product and evidence archive local. Package every component that must execute on Lambda under one transferable `infra/inference/` subtree.

```text
LOCAL PRODUCT AND CANONICAL EVIDENCE
services/app/                  FastAPI orchestration and LLMClient boundary
services/mcp/                  FastMCP and DuckDB tools
web/                           browser application
observability/                 existing local product observability
metrics/inference/             pulled run manifests, scrapes, and derived data
experiments/                   local analysis and notebooks, when added

TRANSFERABLE LAMBDA INFERENCE LAB
infra/inference/
  README.md                    remote lifecycle and exact versions
  gateway/                     remote thin gateway, then #122 control plane
  k8s/
    hami/                      one-GPU slicing when feasible
    workers/                   two same-model vLLM workers
    services/                  cluster-private service discovery
    lmcache/                   added only when real integration is attempted
    mooncake/                  added only when real integration is attempted
  observability/
    prometheus/
    grafana/
    dcgm/
  experiments/                traffic runners that execute near the workers
  scripts/
    sync.sh
    tunnel.sh
    bootstrap.sh
    deploy.sh
    smoke-workers.sh
    run-experiment.sh
    pull-results.sh
    teardown.sh
```

This is the intended end-state layout, not authorization for #120 to prebuild later issues. Issue #120 creates only the cluster, worker, capacity, remote-observability, and lifecycle pieces it needs. Issue #121 adds the thin serve-path gateway and application adapter. Issue #122 fills the remote gateway with real admission, placement, queues, prefix tracking, and overflow policy.

All source remains versioned locally. “Remote” means copied to and executed on Lambda. Only `infra/inference/` is eligible for synchronization; `.env`, credentials, caches, local metrics, the application, MCP, web assets, and durable state are excluded.

## Runtime ownership

```text
Local FastAPI application
  owns agent state, budgets, tool decisions, and each decision to call an LLM
        |
        | OpenAI-compatible request through SSH tunnel
        v
Remote Lambda gateway
  owns guard -> admit -> place -> gateway queue -> overflow -> proxy
        |
        +-----------------------+
        |                       |
        v                       v
same-model vLLM worker A   same-model vLLM worker B
HAMi slice A, if needed    HAMi slice B, if needed
```

The gateway must run beside the workers. Running worker placement and queue management locally would make snapshots and control decisions depend on WAN and SSH latency, weakening the experiment. For controlled measurements, the traffic generator also runs on Lambda. The browser, product application, analysis notebooks, and canonical results remain local.

Prometheus, Grafana, and DCGM execute remotely because they scrape cluster-local services and the GPU. Grafana is viewed locally through a forwarded port. Each experiment writes a remote temporary run directory; `pull-results.sh` copies its manifest and scrapes into `metrics/inference/<run-id>/`, which becomes the canonical evidence location.

## Model-provider boundary

Retain the existing application-owned `LLMClient` abstraction:

```text
LLMClient
  |-- Bedrock adapter
  `-- OpenAI-compatible adapter
        |-- Lambda gateway -> vLLM
        `-- Superlinked/SIE overflow destination
```

Do not introduce LiteLLM Proxy for the initial inference track. Its routing, fallback, budgets, and gateway metrics would overlap the mechanisms the project is intended to implement and measure. A LiteLLM SDK adapter may be reconsidered later if the project acquires several incompatible provider protocols; it must remain behind `LLMClient` and must not take ownership of cluster placement or experimental overflow policy.

Superlinked/SIE is the single planned automatic overflow destination for the controlled overflow experiment. Bedrock remains an explicitly selectable application provider, not an implicit second hop after Superlinked. This keeps outcomes and cost attribution explainable. The overflow policy is adapted from the class9b shape:

- local success stays local;
- `429` policy/rate refusal stays local;
- application `500` stays local;
- slice OOM and configuration failures stay local;
- only explicit capacity outcomes such as `503` or `529` may overflow;
- every overflow preserves the local status and reason in request-correlated metrics.

The provider adapter and end-to-end serve path belong to #121. The real overflow gate belongs to #122. Issue #120 only preserves their network and folder contract.

## SSH and network boundary

Use SSH tunnelling for the initial lab. Do not expose worker ports or unauthenticated vLLM endpoints to the public Internet.

The exact local ports are configurable to avoid collisions. A representative development mapping is:

```text
local 18001 -> Lambda 127.0.0.1:8001   worker A smoke only
local 18002 -> Lambda 127.0.0.1:8002   worker B smoke only
local 18080 -> Lambda 127.0.0.1:8080   gateway, after #121
local 13000 -> Lambda 127.0.0.1:3000   Grafana
```

Native local processes use loopback. Docker Desktop containers use the host bridge name configured by the development environment. Scripts must accept explicit ports and must not stop, replace, or reuse another task's tunnel or local services.

Public HTTPS ingress, a VPN, mTLS, and production identity are separate future decisions. SSH is sufficient for the bounded course experiment and keeps the default attack surface small.

## Worker and HAMi contract

The first routing baseline uses two replicas of the same text model with the same:

- model revision;
- weight dtype or quantization;
- KV-cache dtype;
- maximum model length;
- `max_num_seqs` and `max_num_batched_tokens`;
- prefix-cache setting;
- nominal GPU memory allocation.

Different models or text/vision capabilities would confound least-loaded versus prefix-aware routing. If only one physical GPU is available, begin with two observable 50/50 HAMi-style slices when feasible. Verify the memory visible inside each pod before selecting vLLM's `--gpu-memory-utilization`; do not assume that a HAMi percentage and a vLLM percentage apply to the same physical-memory denominator.

If duplicated model weights leave insufficient KV capacity, or same-GPU compute contention dominates queue and locality effects, move Worker B to another GPU and record the evidence. The goal is an interpretable experiment, not preserving a 50/50 split at all costs.

## KV-cache capacity method

Calculate a paper expectation, then reconcile it with vLLM startup output and live metrics.

For a conventional transformer in which every layer stores full-context keys and values:

```text
KV bytes/token =
  2                         # key plus value
  x num_attention_layers
  x num_key_value_heads
  x head_dim
  x bytes_per_KV_element
```

For one sequence:

```text
sequence KV bytes = KV bytes/token x cached sequence tokens
```

For one worker:

```text
KV budget =
  GPU memory visible to the worker
  - loaded model weights
  - CUDA graphs, activations, allocator/runtime reserve
  - explicit safety headroom

paper sequence ceiling =
  floor(KV budget / (KV bytes/token x assumed tokens/sequence))
```

The analytical formula must state its assumptions. Tensor parallelism, sliding-window or hybrid attention, KV quantization, block rounding, allocator fragmentation, and prefix sharing can change per-device or effective capacity. With single-GPU replicas there is no tensor-parallel division; each worker loads a full model copy. Prefix sharing can reduce aggregate allocated blocks, so the no-reuse calculation is the conservative baseline rather than a prediction of every run.

For each worker, record:

1. GPU model and physical HBM from the host.
2. HAMi memory/core request and memory visible inside the pod.
3. Exact model/revision, parameter count, weight dtype/quantization, and KV dtype.
4. `num_attention_layers`, `num_key_value_heads`, and `head_dim` from model configuration.
5. vLLM flags: `max_model_len`, `max_num_seqs`, `max_num_batched_tokens`, prefix caching, block size, and GPU-memory utilization.
6. Idle HBM before loading, HBM after weights/runtime initialization, and vLLM-reported GPU KV-cache capacity in tokens/blocks.
7. Taxi workload input/context lengths using the same tokenizer: p50, p95, and configured maximum.
8. Paper ceilings at those three lengths and the measured concurrency/TTFT/goodput limit.

Required reconciliation table:

| Quantity | Worker A | Worker B | Source |
|---|---:|---:|---|
| Visible HBM | measured | measured | inside-pod GPU query |
| Weight/runtime footprint | measured | measured | before/after load |
| KV bytes/token | calculated | calculated | model config + KV dtype |
| vLLM KV capacity | measured | measured | startup log/metrics |
| p50/p95 context tokens | measured | measured | workload manifest |
| Paper sequence ceiling | calculated | calculated | formula |
| Configured `max_num_seqs` | configured | configured | manifest |
| First observed limiter | observed | observed | controlled run |

The final `max_num_seqs` is not selected solely from the KV ceiling. Prefill/decode compute, TTFT and deadline SLOs, vLLM scheduling, HAMi contention, and preemption may limit useful concurrency first.

## Issue #120 delivery boundary

Issue #120 proves the remote foundation before application debugging:

1. Synchronize only the bounded inference bundle.
2. Bootstrap the Lambda host and record pinned versions.
3. Install the smallest k3s/HAMi environment needed for two workers.
4. Start two same-model vLLM workers and verify each directly.
5. Expose worker metrics plus GPU/DCGM evidence.
6. Measure cold start, warmup, warm TTFT, KV budget, and the first limiter.
7. Pull a versioned run manifest and raw evidence back to `metrics/inference/`.
8. Tear down reproducibly.

The issue does not implement the application adapter, ReAct split, full control plane, real KV hop, or final experiment matrix. Those remain #121, minimal #115, #122, and #123 respectively.

## Alternatives considered

### Transfer the entire repository

Rejected. It copies unrelated product code and secrets-adjacent local configuration, weakens the deployment contract, and makes short-lived Lambda setup slower and harder to reproduce.

### Keep the cluster gateway local

Rejected for placement, queueing, and experiment control. WAN latency would make worker snapshots stale and would contaminate queue/TTFT measurements. Local code may select the configured provider, but worker control remains remote.

### Deploy LiteLLM Proxy as the local or remote gateway

Deferred. It provides useful provider normalization, but its router, fallback, budgets, and metrics overlap the mechanisms under study. The current `LLMClient` plus one OpenAI-compatible adapter is the smaller boundary.

### Run experiment traffic from the developer machine

Rejected for controlled serving measurements because SSH and WAN latency would be part of every result. It remains acceptable for end-to-end manual smoke tests.

### Treat the class9b topology as production-ready

Rejected. Its k3s/HAMi/tunnel mechanics are reusable, but its capability split and metadata-only KV-hop demonstration do not satisfy the same-model routing or real-hop proof required here.

## Consequences

### Positive

- One directory is sufficient to recreate and remove the remote lab.
- Local product ownership remains unchanged.
- Worker routing and queue measurements occur close to the engines.
- Provider switching stays behind an existing application interface.
- Remote raw evidence has a defined path back into the repository.
- Future LMCache/Mooncake claims must be supported by observed transfer evidence.

### Costs and limitations

- Some observability and experiment code lives under `infra/inference/` because execution locality is more important than a purely conceptual folder taxonomy.
- SSH tunnels are development infrastructure, not production ingress.
- Two replicas on one GPU duplicate weights and may not yield an interpretable routing experiment.
- The exact model, GPU, slice size, and vLLM tuning remain measured outputs of #120 rather than values fixed by this ADR.

## References

- [Inference project plan](../inference-project-plan.md)
- [Lambda Cloud firewall documentation](https://docs.lambda.ai/public-cloud/firewalls/)
- [Lambda Cloud SSH tunnel documentation](https://docs.lambda.ai/public-cloud/on-demand/connecting-instance/)
- [HAMi GPU virtualization principles](https://project-hami.io/docs/core-concepts/gpu-virtualization)
- [vLLM OpenAI-compatible serving](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [vLLM production metrics](https://docs.vllm.ai/en/latest/usage/metrics/)
- [Superlinked Inference Engine](https://github.com/superlinked/sie)
- [LiteLLM overview](https://docs.litellm.ai/docs/)

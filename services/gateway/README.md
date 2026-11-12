# Inference Gateway

The owned FastAPI gateway controls **admission, scheduling, routing, and overflow** for self-hosted model requests. It never loads a model or generates tokens.

## Request pipeline

```text
/serve
  → request/window guard
  → tenant quota
  → admission or shed
  → worker placement (load + prefix affinity)
  → per-worker priority queue
  → KV reuse/transfer decision
  → vLLM worker or permitted overflow
```

The gateway exposes `/serve`, `/v1/chat/completions`, `/tokenize`, `/metrics` and `/health`. Per-stage decision headers and Prometheus counters explain rejection, queue time, placement, and resource pressure.

**Ownership:** service policy and routing live in [this directory](./); Kubernetes deployments, vLLM workers, HAMi, KV transfer, and observability live under [inference infrastructure](../../infra/inference/).

**Evidence:** [Inference architecture](../../DESIGN.md) · [Experiment results](../../docs/inference-experiments/inference-evidence-index.md).

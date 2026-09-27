# Isolated Lambda Inference Lab (Issue #120)

This directory contains the self-contained, isolated cluster bundle for running two symmetric
vLLM workers on a Lambda GPU instance with k3s, HAMi GPU virtualization, and cluster observability.

Per [ADR 0010](../../docs/decisions/0010-transferable-lambda-inference-lab.md) and
[docs/inference-project-plan.md](../../docs/inference-project-plan.md), this directory is the **only**
source transferred to and executed on Lambda. The existing FastAPI application, FastMCP DuckDB service,
web UI, product observability, and durable state remain local.

---

## Architecture and Security Boundary

```text
Local Machine (Operator)
  Makefile targets / Python runners
  SSH Local Forwards:
    - 18001 -> Lambda 127.0.0.1:8001 (Worker A)
    - 18002 -> Lambda 127.0.0.1:8002 (Worker B)
    - 13000 -> Lambda 127.0.0.1:3000 (Grafana)
       |
       | SSH tunnel (key authentication only)
       v
Lambda GPU Host
  k3s + HAMi Scheduler (50/50 virtual GPU slice)
  Namespace: inference-lab (ClusterIP only, no public ingress, no NodePort)
  ├── Service: inference-worker-a:8000 -> Pod: vLLM Worker A (Qwen3-0.6B)
  ├── Service: inference-worker-b:8000 -> Pod: vLLM Worker B (Qwen3-0.6B)
  ├── Service: dcgm-exporter:9400      -> DaemonSet: DCGM GPU metrics
  ├── Helm: Prometheus Server:80      -> Scrapes workers, HAMi, DCGM
  └── Helm: Grafana:80                -> Cluster observability dashboards
```

### Security and Isolation Invariants
- **No repository transfer**: `rsync` syncs only `infra/inference/`.
- **No secret transmission**: Local `.env` is never copied as a file. Private keys and tokens are strictly excluded.
- **ClusterIP only**: All pods and services bind internal cluster IPs. No `hostPort`, `hostNetwork: true`, or `NodePort` are permitted.
- **Loopback port forwarding**: Remote `kubectl port-forward` binds strictly to `127.0.0.1`.
- **Lambda firewall**: Only port 22 (SSH) is reachable externally. Negative smoke tests assert that 8000, 8001, 8002, and 3000 reject direct connections.
- **Symmetric workers**: Worker A and B use identical container images, model weights (`Qwen/Qwen3-0.6B`), revisions, context lengths (8192), batching limits, and 50% HAMi allocations.

---

## Directory Layout

```text
infra/inference/
  ├── .env.example              # Tracked template with pinned safe defaults
  ├── README.md                 # This operator guide
  ├── k8s/                      # Kubernetes manifests
  │   ├── namespace.yaml        # Dedicated 'inference-lab' namespace
  │   ├── hami/                 # HAMi 50/50 slice configuration spec
  │   │   └── worker-slices.yaml
  │   ├── workers/              # Symmetric vLLM Deployments
  │   │   ├── worker-a.yaml
  │   │   └── worker-b.yaml
  │   └── services/             # ClusterIP Services
  │       ├── worker-a.yaml
  │       └── worker-b.yaml
  ├── observability/            # Metrics and monitoring configuration
  │   ├── prometheus/values.yaml# Prometheus Helm values with worker/DCGM scrape configs
  │   ├── grafana/values.yaml   # Grafana Helm values with ClusterIP & dashboard sidecar
  │   └── dcgm/dcgm-exporter.yaml # DCGM DaemonSet with ClusterIP service
  ├── experiments/              # Capacity, warmup, and probe runners
  │   ├── warmup.py             # Cold/warm TTFT measurements and percentiles
  │   ├── capacity.py           # KV cache math and paper ceiling calculations
  │   ├── probe.py              # Single-worker health, model, completion, metrics probe
  │   └── evidence.py           # Deterministic run manifest writer
  ├── scripts/                  # Operator lifecycle shell scripts
  │   ├── validate.sh           # Local manifest & configuration validation
  │   ├── sync.sh               # Safe rsync to Lambda
  │   ├── config.sh             # Allowlisted ConfigMap generation
  │   ├── secret.sh             # Stream optional HF_TOKEN directly to k8s Secret
  │   ├── bootstrap.sh          # Pinned k3s, Helm, and HAMi installation
  │   ├── deploy.sh             # Deploy manifests, Prometheus, Grafana, and DCGM
  │   ├── tunnel.sh             # Establish loopback SSH forwards
  │   ├── smoke.sh              # Worker smoke and negative security tests
  │   ├── pull-evidence.sh      # Rsync run artifacts into metrics/inference/<run-id>
  │   └── teardown.sh           # Explicit removal of issue-owned resources
  └── tests/                    # Contract tests
      ├── test_scripts_contract.py
      └── test_manifests_contract.py
```

---

## Quickstart Operator Guide

### 1. Configuration
Create a local `.env` file from the template (this file is git-ignored):
```bash
cp infra/inference/.env.example infra/inference/.env
```
Populate `infra/inference/.env` with your Lambda instance details:
```bash
LAMBDA_SSH_HOST=192.0.2.1
LAMBDA_SSH_USER=ubuntu
LAMBDA_SSH_KEY_PATH=~/.ssh/lambda_key.pem
```

### 2. Validation
Locally validate all Kubernetes manifests and configuration contracts:
```bash
make inference-validate
```

### 3. Cluster Provisioning (`inference-up`)
Run the combined provisioning flow (sync -> config -> bootstrap -> deploy):
```bash
make inference-up
```
Or execute the individual steps for granular control:
```bash
make inference-sync        # Sync infra/inference bundle to Lambda
make inference-config      # Apply safe allowlisted config map remotely
make inference-secret      # (Optional) Stream HF token if using gated model
make inference-bootstrap   # Install k3s, Helm, and HAMi on Lambda
make inference-deploy      # Deploy workers, DCGM, Prometheus, and Grafana
```

### 4. Connect and Open Safe Tunnel
Open the loopback SSH tunnel in a dedicated terminal window:
```bash
make inference-tunnel
```
Alternatively, `make inference-connect` will sync latest files and launch the tunnel.

Forwarded local ports:
- `http://127.0.0.1:18001` -> Worker A (vLLM OpenAI API)
- `http://127.0.0.1:18002` -> Worker B (vLLM OpenAI API)
- `http://127.0.0.1:13000` -> Grafana (user: `admin`, password: `change-me-locally`)

### 5. Verification and Smoke Testing
With the tunnel running, execute worker health and negative security checks:
```bash
make inference-smoke
```
This validates:
- Worker A `/health`, `/v1/models`, `/metrics`, and `/v1/completions`
- Worker B `/health`, `/v1/models`, `/metrics`, and `/v1/completions`
- Negative test: asserts that ports 8000, 8001, 8002, 3000 on `$LAMBDA_SSH_HOST` reject direct outside connections.

### 6. Run Warmup & Capacity Experiments (`inference-run`)
Execute the measurement suite:
```bash
make inference-run
```
Or run each runner independently:
```bash
make inference-warmup      # Measure cold TTFT vs warm p50/p95 TTFT
make inference-capacity    # Calculate paper sequence ceilings & verify vLLM blocks
```

### 7. Pull Evidence
Retrieve the experiment run artifacts to local canonical storage:
```bash
make inference-pull-evidence RUN_ID=run-20260927-01
```
Evidence lands in `metrics/inference/<run-id>/` including `run-manifest.json`, scrapes, and logs.

### 8. Teardown
When finished, tear down the remote cluster resources to stop GPU resource usage:
```bash
make inference-teardown
```

---

## Cost and Teardown Warning
Cloud GPU instances are billed per minute or hour. Always run `make inference-teardown` when
experiments are completed, and terminate or stop the Lambda instance from the Lambda dashboard.

---

## Boundary to Future Issues

### Downstream Provider Boundary
```text
Existing LLMClient abstraction
  |-- Bedrock adapter
  `-- OpenAI-compatible adapter
        |-- Lambda gateway/vLLM
        `-- Superlinked overflow
```

- **Issue #120 (This Lab)**: Self-contained Lambda/k3s cluster, two symmetric workers, HAMi slicing, cold/warm TTFT warmup runner, KV capacity calculations, and evidence retrieval. **Does not implement application adapters, gateway routing, or Superlinked overflow.**
- **Issue #121**: Thin `/serve` path, FastAPI `LLMClient` adapter, and OpenAI-compatible client integration.
- **Issue #115**: Deterministic and ReAct taxi analytics workload traces.
- **Issue #122**: Control-plane gateway: guard, admission, placement, queueing, and capacity-only (`503`/`529`) Superlinked overflow policies. Superlinked keys and fallback configuration are introduced here.
- **Issue #123**: Controlled A/B experiments (least-loaded vs prefix-aware routing) and final thesis proof.


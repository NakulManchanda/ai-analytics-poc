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

### 1a. vLLM tool-calling flags
Both `k8s/workers/worker-a.yaml` and `worker-b.yaml` pass `--enable-auto-tool-choice
--tool-call-parser hermes` to vLLM so the gateway can forward OpenAI-style `tools`/
`tool_choice` payloads without a 400. This is a manifest-only change in this PR; it takes
effect on the next fresh `inference-up` deploy, not on an already-running cluster.

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
If the pulled run has no capacity request-results file (e.g. a cluster-snapshot-only pull), the
`run-manifest.json` summarizer step is skipped with a clear message instead of failing.

### 7b. Regenerate Grafana Dashboards (#115 slice E)
The dashboards under `observability/grafana/dashboards/` are generated, not hand-edited.
Regenerate and commit them after changing `observability/grafana/dashboards.py`:
```bash
make inference-dashboards
```
This writes deterministic JSON (sorted keys, stable panel ids/uids) for:
- **KV & Prefix Cache** (`kv_prefix_cache.json`): KV usage %, prefix-cache hit ratio, hits/queries
  per second, preemptions per second, and a `cache_config_info` table, all split per worker.
- **Prefill vs Decode** (`prefill_decode.json`): prefill/decode/queue/inference time p50/p95, TTFT,
  inter-token latency, prompt vs generation tokens/s, per-step `iteration_tokens_total`
  distribution, prompt/generation token-count distributions, and prefill time share of
  prefill+decode time, all split per worker.
- **Scheduler & Concurrency** (`scheduler_concurrency.json`): running vs waiting, a slot-saturation
  line (`running / max_num_seqs`, read from the worker manifests), e2e p95, and success rate by
  `finished_reason`, all split per worker.
- **GPU & HAMi Slices** (`gpu_slices.json`): DCGM GPU/memory-copy utilization, framebuffer used/free,
  power (device-wide, since HAMi splits compute/memory quota, not DCGM's own telemetry), plus
  inference-lab pod restarts, ready replicas, and pod phase (kube-state, split per pod).
- **Cluster** (`cluster.json`): the same node/pod/container/DCGM overview as before this change.

Every worker-scoped vLLM panel is split by the `instance` label — the only label that
distinguishes worker A from worker B in these series, because Prometheus scrapes both workers as
two static-config targets rather than via per-pod service discovery (see
`observability/prometheus/values.yaml`). Kube-state and cAdvisor panels are split by `pod` instead.

#123 slice B adds gateway-backed dashboards (Prometheus scrapes the gateway `/metrics` as job
`inference-gateway`, `inference-gateway.inference-lab:8080`): **Overview** (`overview.json`, with a
goodput *proxy* = share of `200`s, not SLO-aware; true goodput comes from the replayer artifacts),
**Gateway & Admission**, **Router & Placement**, **Queues** (incl. p99 batch-minus-interactive
spread), **Overflow**, **Memory Proof** (DCGM framebuffer with KV usage, running/waiting,
request rate, preemptions, prefix hit ratio on one time axis), and a text-only **KV Hop stub**
(real hop metrics land with #133).

#### Alerts (#123 slice B)
Five rules live in `observability/prometheus/alerts.yaml` (standard rule-group format) and are
loaded by `deploy.sh`, which nests them under `serverFiles.alerting_rules.yml` in a temporary values file passed with `-f`; Alertmanager stays disabled, so they show in the Prometheus
UI only. Four are the required production alerts, plus one supplemental engine alert:
- `InferenceKVPressureSustained`: KV usage > 85% for 5m; above this vLLM starts queueing/preempting.
- `GatewayInteractiveTTFTSLOBreach` (the TTFT SLO alert): p99 of
  `gateway_ttft_seconds{class="interactive"}` > 0.1s for 5m. The histogram is observed in the
  gateway at the first streamed chunk with non-empty content (role-only deltas, keepalives, errors
  and `[DONE]` are ignored), local or overflow. Non-streaming requests are not measured, so this
  alert covers streaming traffic only. It includes gateway queue time.
- `GatewayQueueShedSurge`: `orch_shed_total` + `timeout_queue` rejects > 0.5/s for 5m.
- `InferenceWorkerIntegrity` (worker/target integrity): worker not healthy, snapshot age > 15s
  (gateway stale threshold is 5s), preemptions > 0.1/s, any gateway/worker scrape target `up == 0`,
  gateway `up` series absent, or fewer than 2 workers up, for 2m.
- `InferenceEngineTTFTHigh` (supplemental): engine-side p95 `vllm:time_to_first_token_seconds` >
  0.1s for 5m. Mixes classes and excludes gateway queue time, so it is not the SLO alert.

Contract tests for the generator live in `tests/test_dashboards_contract.py` and run with the
rest of the inference test suite:
```bash
uv run --project services/app pytest infra/inference/tests tests/inference -q
```

### 7c. Opt-in real KV transfer (#133)

The #133 path keeps the two HAMi workers and adds LMCache 0.3.9 inside the pinned vLLM 0.11.0
image. Mooncake contributes a shared host-memory pool and transports blocks over TCP. The worker
connector derives its cache namespace from exact rendered token IDs, immutable model/tokenizer
revisions, the prompt contracts, dtypes, adapter namespace and vLLM block layout. Client labels
never become cache keys.

Build and push an immutable image tag, then render the bundle for review:

```bash
make inference-kv-image KV_IMAGE=registry.example/ai-inference-kv:0.11.0-lmcache0.3.9
docker push registry.example/ai-inference-kv:0.11.0-lmcache0.3.9
make inference-kv-render \
  KV_IMAGE=registry.example/ai-inference-kv:0.11.0-lmcache0.3.9 \
  CACHE_NAMESPACE=e5-20261008-01 \
  TEMPLATE_VERSION=taxi-chat-v1 \
  PREFIX_CONTRACT_VERSION=prefix-v1 \
  OUT=work/kv-hop.yaml
```

The render command does not apply anything. Inspect `work/kv-hop.yaml`, sync it to the isolated
host, and apply it only during an authorized GPU session. It enables forced placement and the
bounded experiment headers for the four-case proof; restore the normal gateway manifest after
the experiment. Mooncake remains a ClusterIP service and must not be exposed outside the lab.

The proof runner requires explicit topology and version JSON files. Copy
`mooncake/topology.example.json` and `mooncake/versions.example.json` into `work/`, replace every
placeholder from the deployed cluster, and keep the cache namespace identical to the rendered
bundle. Run it on a host where `kubectl` addresses the lab and the gateway plus both worker
endpoints are reachable. After `make inference-sync`, the equivalent remote command is
`python mooncake/smoke.py ...` from the synced `infra/inference` directory.

```bash
make inference-kv-smoke \
  GATEWAY_URL=http://127.0.0.1:18080 \
  WORKER_A_URL=http://127.0.0.1:18001 \
  WORKER_B_URL=http://127.0.0.1:18002 \
  TOPOLOGY=work/kv-topology.json \
  VERSIONS=work/kv-versions.json \
  OUT=metrics/inference/kv-hop-20261008-01
```

The runner retains deployment state, request metadata, raw pre/post metrics and worker logs. It
passes only when it distinguishes same-worker reuse, cross-worker recompute with transfer off,
an independently warmed destination hit, and a real Mooncake retrieval with positive tokens,
bytes and post-forward destination consumption. A worker header or lower latency cannot pass.

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

# Inference Lab: Testing, Benchmarking & Failure Triage Guide

This guide details **what to test, how to run benchmark scenarios, how to verify telemetry, and how to triage failures** across the inference cluster. It bridges the engineering implementation in `services/app` and `infra/inference` with the core thesis in `docs/inference-project-plan.md`.

---

## 1. The Three-Plane Testing & Diagnostic Model

When benchmarking or triaging issues on the inference cluster, decompose the system into three distinct planes:

```
+-----------------------------------------------------------------------------------+
|  1. DATA PLANE (I/O, Transport, App)                                             |
|     - FastMCP / DuckDB tools, SQL queries, retrieval/catalogue, HTTP streaming   |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|  2. CONTROL PLANE (Inference Gateway)                                            |
|     - Guardrails (guard.inspect)  -> Input length, sanitization                  |
|     - Admission (should_shed)     -> Queue wait vs deadline estimation (504/429) |
|     - Placement (place.pick)      -> least_loaded vs prefix_then_load             |
|     - Per-Worker Queues           -> Concurrency backpressure                     |
|     - Hop Store Check             -> Recompute vs Mooncake/LMCache transfer       |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|  3. GPU PLANE (Compute & Memory)                                                 |
|     - vLLM continuous batching scheduler (waiting/running/swapped)                |
|     - Prefill phase (chunked prefill, tensor bandwidth)                           |
|     - KV Cache read/write (paged attention, prefix caching blocks)               |
|     - Decode phase (autoregressive memory-bandwidth-bound token generation)       |
|     - Hardware: NVIDIA A100 SXM4-40GB, HAMi 50/50 virtual GPU partitions         |
+-----------------------------------------------------------------------------------+
```

### Triage Order
When an SLO regresses (e.g. doubled TTFT or end-to-end latency spike):
1. **Decompose the latency into spans**: Total Duration = Tool Time + Gateway Queue + vLLM TTFT + vLLM Decode.
2. **Inspect the Data Plane first**: Did tool calls, DuckDB queries, or retrieval search latencies inflate?
3. **Inspect the Control Plane second**: Did requests queue at the gateway? Were admission deadlines shed? Was traffic routed to an overloaded worker?
4. **Inspect the GPU Plane last**: Did vLLM prefill time jump? Did KV cache hit 100% and trigger preemptions? Did ITL (inter-token latency) spike?

---

## 2. Test Execution Workflow

### 2.1 Cluster Bring-up & Verification
Before running benchmarks, ensure the Lambda GPU instance is running and the tunnel is open:

```bash
# 1. From repository root (or worktree)
make inference-status     # Verify remote host connectivity

# 2. Deploy or update cluster manifests if necessary
make inference-deploy

# 3. Open the loopback SSH tunnel in a dedicated terminal
make inference-tunnel
```

Forwarded Local Endpoints:
- `http://127.0.0.1:18080`: Inference Gateway (`/serve`, `/v1/chat/completions`)
- `http://127.0.0.1:18001`: Worker A (vLLM API & `/metrics`)
- `http://127.0.0.1:18002`: Worker B (vLLM API & `/metrics`)
- `http://127.0.0.1:19090`: Prometheus Server
- `http://127.0.0.1:13000`: Grafana Dashboards

Verify both workers are healthy:
```bash
curl -s http://127.0.0.1:18001/health
curl -s http://127.0.0.1:18002/health
curl -s http://127.0.0.1:18080/health
```

---

## 3. What to Test: The 6 Controlled Experiments

The following 6 controlled experiments establish the core findings for the project:

### Experiment 0: Paper Capacity Math
- **Target**: Calculate exact $\text{bytes} / \text{token}$ for the deployed model (e.g., Qwen 2.5 Coder 7B FP16):
  $$\text{KV Bytes/Token} = 2 \times \text{layers} \times \text{kv\_heads} \times \text{head\_dim} \times 2 \text{ bytes}$$
- **Budget**: On a 20GB HAMi virtual GPU slice, subtract model weights (~15GB) and CUDA workspace (~1GB), leaving ~3.5GB for KV cache.
- **Hypothesis**: Calculate max concurrent sequences at taxi application lengths (~1,500–3,000 tokens) vs theoretical `max_model_len`.

### Experiment 1: Cold vs. Warm Worker
- **Target**: Measure time from container launch to first served token.
- **What to Observe**: Model weights loading time, CUDA graph capture time, and cold TTFT vs warm TTFT.

### Experiment 2: Two-Worker Baseline & KV Pressure
- **Target**: Send identical workload bursts through the gateway to establish the single-worker baseline (Worker A active, Worker B idle control).
- **Tool**: `run_scenario.py` with pre-burst and post-burst Prometheus delta scrapes.

### Experiment 3: Routing Comparison (`least_loaded` vs `prefix_then_load`)
- **Target**: Compare naive queue-depth routing against prefix-affinity routing on multi-turn conversations.
- **Test**:
  - Run `growing_multi_turn.json` under `least_loaded`: Turn 1 goes to Worker A, Turn 2 routes to Worker B because Worker A is busy $\to$ cold recompute on Worker B.
  - Run `growing_multi_turn.json` under `prefix_then_load`: Turn 2 routes to Worker A holding the prefix cache $\to$ zero prompt recompute, sub-30ms TTFT.
- **Metric**: Prefix hit ratio delta, prompt throughput ($\Delta \text{tokens}/\text{s}$), and total TTFT.

### Experiment 4: Admission Control & Deadline Shedding (`should_shed`)
- **Target**: Protect goodput under overload.
- **Test**: Inject concurrent traffic with tight deadlines (`x-deadline-ms: 150`).
- **Validation**: When $\text{Estimated Queue Wait} + \text{Prefill Time} > 150\text{ ms}$, gateway returns `504` or `429` with `x-guard-decision: shed`.
- **Proof**: Scrape vLLM engine metrics to prove shed requests **never entered vLLM's engine queue** (`vllm:num_requests_waiting` does not increase).

### Experiment 5: Recompute vs. Mooncake / LMCache Hop
- **Target**: For cross-worker dispatch, find the crossover point where transferring KV blocks across the fabric is faster than recomputing the prompt.
- **Metric**: Plot latency vs prefix length (e.g. 500 tokens, 1,500 tokens, 4,000 tokens) for Recompute vs Hop.

---

## 4. Running Benchmark Scenarios with the Replayer

The scenario runner (`services/app/scripts/run_scenario.py`) executes declarative JSON scenarios, captures client-side TTFT and token metrics, and computes Prometheus monotonic deltas.

### Available Pre-canned Scenarios:
1. `config/scenarios/shared_prefix_fanout.json`: 20 independent conversations sharing the global system prompt. Tests prefix cache fanout.
2. `config/scenarios/growing_multi_turn.json`: 5 sequential turns testing conversational history prefix retention.
3. `config/scenarios/concurrent_contention.json`: 4 concurrent multi-turn conversations competing for engine slots.
4. `config/scenarios/strategy_comparison.json`: Comparative sequence across agent strategies.

### Running a Scenario via Make Targets:
```bash
# Activate virtual environment
source vev/bin/activate

# Run shared prefix fanout benchmark against gateway
make replay-shared-prefix

# Run growing multi-turn conversation benchmark
make replay-growing-multi-turn

# Run concurrent contention benchmark
make replay-contention

# Run direct CLI with custom parameters
uv run --project services/app python services/app/scripts/run_scenario.py \
  --scenario concurrent_contention \
  --target-url http://localhost:18080 \
  --metrics-url http://localhost:18001/metrics \
  --concurrency 4 \
  --output-dir metrics/evidence
```

### Scenario CLI Parameters:
- `--scenario`: Scenario name (without `.json`) or path to custom JSON scenario.
- `--target-url`: Target server (`http://localhost:18080` for Gateway, `http://localhost:8000` for App).
- `--metrics-url`: Prometheus metrics endpoint (`http://localhost:18001/metrics` for Worker A).
- `--concurrency`: Override concurrency semaphore limit.
- `--endpoint-type`: `gateway_chat` (direct OpenAI `/v1/chat/completions`) or `app_runs` (FastAPI `/api/runs` with SSE).
- `--no-sse`: Fall back to conversation polling instead of SSE event streaming.
- `--output-dir`: Destination directory for timestamped JSON results and Markdown reports.

---

## 5. Telemetry & Grafana Dashboard Verification

While scenarios run, observe the live dashboards at `http://127.0.0.1:13000`:

| Dashboard | Panels to Verify | What Healthy Behavior Looks Like |
|---|---|---|
| **Cluster Overview** | `vLLM Workers Up (1=Healthy)` | Both Worker A and Worker B show solid green `1`. |
| | `Node Ready` | Host VM shows `1`. |
| **KV & Prefix Cache** | `KV cache usage % per worker` | Rises under concurrent load; stays $< 80\%$ (below memory pressure threshold). |
| | `Prefix cache hit ratio per worker` | Steps up from 0% (cold start) $\to$ 15% $\to$ ~30%+ on shared prompts. |
| | `Preemptions / s` | Stays at **0.00**; non-zero indicates memory thrashing and evicted KV blocks. |
| **Prefill vs Decode** | `TTFT (p50 / p95)` | $p50 < 30\text{ ms}$; $p95 < 60\text{ ms}$ with active prefix caching. |
| | `ITL (time per output token)` | Steady at ~5.5–9.5 ms/token (~105–180 tokens/sec streaming cadence). |
| | `Prefill share of total time` | Drops to $< 2\%$ on multi-turn conversations (proves decode-bound workload). |
| | `Queue time (p50 / p95)` | Rises under concurrency bursts (~150–290 ms during 4-way contention). |

---

## 6. How to Measure Goodput vs. Throughput

Under overload, raw throughput ($\text{req}/\text{s}$ or $\text{tokens}/\text{s}$) can appear deceptively high while user experience has collapsed. 

### The Goodput Metric:
$$\text{Goodput} = \frac{\text{Completed Requests with } TTFT \le SLO_{TTFT} \text{ and } TotalTime \le SLO_{E2E}}{\text{Elapsed Benchmark Time (seconds)}}$$

- **Target SLOs for NYC Taxi Agent**:
  - $SLO_{TTFT} \le 100\text{ ms}$ (interactive responsiveness).
  - $SLO_{E2E} \le 3.5\text{ s}$ (turn completion).
- Any request that returns HTTP 200 after 5 seconds is counted as **throughput**, but **zero goodput**.
- Plot Goodput vs. Offered Concurrency ($N=1, 2, 4, 8, 16$). The point where Goodput flattens or drops while Raw Throughput continues rising marks the **true cluster saturation limit**.

---

## 7. Failure Plane Triage Case Studies

### Case 1: Silent KV Corruption Under Memory Pressure
* **Symptom**: P99, error rates, and OOM counters look normal (HTTP 200), but generated answers are corrupted, truncated, or hallucinated.
* **Root Cause**: Fleet load exceeding 0.85–0.95; TOCTOU race where a KV block was available when checked, but preempted before decode completed.
* **Triage & Mitigations**:
  - Check `vllm:num_preemptions_total` in Prometheus scraper delta.
  - Set gateway admission ceiling to cap worker load at 0.80–0.85.
  - Run post-generation quality validation (schema checks on tool arguments, repetition penalties).

### Case 2: Stale RAG Index / Tool Bottleneck with Doubled TTFT
* **Symptom**: Client TTFT doubles from 300 ms to 700 ms; GPU utilization is ~75% and DCGM is clean.
* **Root Cause**: Data plane failure. Tool execution / retrieval reranking latency jumped (e.g. from 95 ms to 690 ms), while GPU prefill remained at 28 ms.
* **Triage**: Decompose end-to-end trace into tool execution span vs GPU TTFT span. Do not optimize GPU kernels when the data plane is the bottleneck.

### Case 3: Prefix Routing Imbalance
* **Symptom**: Worker A hits 99% KV cache usage and begins thrashing/preempting while Worker B sits at 0% or 40%.
* **Root Cause**: Naive prefix affinity routing sends all shared-prefix traffic to Worker A without a load cap.
* **Triage**: Implement exception-based spillover in the gateway router (`prefix_then_load` with a 70% KV threshold): when Worker A exceeds 70% KV usage, spill traffic to Worker B even though it requires a cold prefill.

---

## 8. Teardown & Cost Management
When testing concludes, always shut down remote resources to avoid cloud compute charges:

```bash
# 1. Terminate local tunnel
pkill -f "ssh.*18080:127.0.0.1:8080"

# 2. Stop or terminate the Lambda instance via Lambda Cloud Console
#    (or run make inference-teardown if running automated provisioning)
```

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
5. `config/scenarios/e3_routing_mixed.json`: 10 multi-turn taxi conversations sharing the app's real system prefix (E3 headline, gateway_chat). `e3_routing_large_prefix.json` is the synthetic large-prefix variant.
6. `config/scenarios/e4_admission_overload.json`: interactive, noisy-tenant and batch long-context traffic (E4, gateway_chat).

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

### Running E3/E4 Controlled Runs (test-only gateway controls)

E3 (`e3_routing_mixed`) and E4 (`e4_admission_overload`) replay the *same* trace under two settings, chosen per run by the replayer instead of redeploying. The gateway honors the control headers `x-placement-policy-override` (`round_robin|least_loaded|p2c|prefix_then_load`), `x-admission-mode` (`on|off`) and `x-tenant-quota-mode` (`on|off`) **only** when it runs with `ALLOW_EXPERIMENT_CONTROLS=1`; otherwise they are ignored. With controls enabled, an invalid value returns `400 invalid_experiment_control`. Applied overrides are echoed as `x-policy-override-applied` / `x-admission-mode` response headers, recorded per request by the replayer, and written to the run manifest and the gateway decision log.

- **Test-only.** The shipped manifest sets `ALLOW_EXPERIMENT_CONTROLS=0`. Enable it only for the duration of an experiment (`kubectl set env deploy/... ALLOW_EXPERIMENT_CONTROLS=1`, or edit the manifest), and turn it back off afterwards. Never trust these headers from production traffic.
- `x-admission-mode: off` skips only `should_shed` (capacity/deadline). The guard and the tenant quota still run, so E4's noisy tenant still exercises fairness; add `x-tenant-quota-mode: off` (not sent by the replayer) to disable that too. The forced-worker header keeps precedence over a policy override.
- **E4 tenants.** Set `TENANT_ALLOWLIST=tenant_interactive,tenant_noisy,tenant_batch` (unknown tenants share one `other` bucket). `TENANT_MAX_CONCURRENCY` (default 4) and `TENANT_TOKEN_BUDGET` control how hard `tenant_noisy` (12 conversations) is limited. Tune the admission thresholds (`MAX_DECODE_SLOTS`, `KV_FREE_MIN`, `PREFILL_TOKENS_PER_S`, `QUEUE_WAIT_PER_WAITING_S`; see `AdmitConfig.from_env`) so the trace actually overloads the two workers.
- **Verified controls.** When a control is requested, the replayer requires the gateway to echo it (`x-policy-override-applied`, `x-admission-mode`). If the first response does not (typically `ALLOW_EXPERIMENT_CONTROLS` is off), the run aborts with exit code 2 and writes no evidence; a later missing/mismatched echo marks that turn `control_not_applied` (failed, not good). The manifest records `*_requested`, `*_verified` (true only with at least one matched echo and no mismatch; false if none was ever observed, e.g. every request failed before headers) and `control_observations` / `control_unverified_turns`. Controls with an `app_runs` scenario exit 2 immediately, since they can never be verified.
- **What E3 measures.** Shared *system-prefix* affinity only. The replayer sends `x-prefix-id` = hash of the system prefix and `x-prefix-tokens` = estimated tokens of that region; the gateway stores that (clamped to the prompt estimate) as the reusable belief. The overlap gate compares believed-reusable tokens with the current prompt's estimated tokens and requires >= 0.8 (`STICKY_OVERLAP`, a code constant). Without `x-prefix-tokens` the gateway keeps the legacy behavior, which counts the whole prompt as reusable and over-counts prompts with per-turn suffixes or divergent history.
- **Headline vs synthetic E3.** `e3_routing_mixed` is the **headline trace used for the E3 answer**: its `system_prefix` is the app's real global prefix (system prompt + taxi rules + dataset schema, once; `app.benchmarks.canonical_prefix`, guarded by a drift test), about 270 estimated tokens. Worst-case per-turn overlap (chars/4 estimate, replies at `max_tokens` 128) is turn 1 ~0.90, turn 2 ~0.59, turn 3 ~0.44, turn 4 ~0.35, turn 5 ~0.29, so `prefix_then_load` is expected to stick on turn 1 and spill to load-based placement (`prefix_overlap_low`) afterwards. **That is a finding, not a bug**, and the workload is deliberately not tuned to the gate. `e3_routing_large_prefix` is a separate **synthetic prefix-size treatment** (same conversations, prefix padded to ~6k tokens by repetition; overlap >= 0.8 on every turn) that shows affinity when the prefix dominates; use `make replay-e3-large-prefix-least-loaded` / `replay-e3-large-prefix-prefix-then-load`. History-aware prefix identity (system + prior completed turns, per `docs/prefix-contract.md`) is the follow-up that would keep affinity across growing conversations; it is not measured here.
- **Evidence of the crossover.** Per-request `gateway_headers` include `x-placement-reason`; `summary.json` has `placement_reason_by_turn` and the Markdown report a "Placement reasons by turn" table. The manifest's `system_prefix` records `prefix_chars`, the chars/4 *estimate* and, when `/tokenize` is reachable (`--tokenize-url` / `TOKENIZE_URL`, default `<target-url>/tokenize`), the *exact* token count of the raw prefix text (else `exact_tokens: null` plus a reason). Only the scenario-level `system_prefix` is recorded.
- **Context budget.** Workers run `--max-model-len 8192` and vLLM rejects prompt + `max_tokens` above it, so each gateway scenario sets `max_tokens` (default 512; 128 in E3/E4) and a test asserts that, per turn, system prefix + sum of prior (question + max_tokens) + current question + max_tokens <= 8192 - 256 for E3 (both variants) and E4 (gateway chars/4 estimate, approximate; the margin absorbs the error).
- **Prompts.** A scenario/conversation `system_prefix` makes `gateway_chat` send `[system prefix, prior turns (incl. streamed replies), question]` with a stable `x-prefix-id` (SHA-256 prefix of the shared prefix). Without it the bare question is sent as before.

```bash
make replay-e3-least-loaded        # same E3 trace, --policy-override least_loaded, --label e3-least_loaded
make replay-e3-prefix-then-load    # same E3 trace, --policy-override prefix_then_load
make replay-e3-large-prefix-least-loaded / replay-e3-large-prefix-prefix-then-load  # SYNTHETIC large-prefix treatment
make replay-e4-admission-on        # E4 trace with --admission-mode on
make replay-e4-admission-off       # E4 trace with --admission-mode off
# all take TARGET_URL=... METRICS_URL=... REPLAYER_FLAGS="--sweep-concurrency 4,8,16"
```

Compare `summary.json` goodput (`by_worker`, `by_tenant`, `by_workload_class`) between the paired runs; `manifest.json` records `policy_override`, `admission_mode` and `policy_under_test` (the label). `manifest.scenario.sha256` hashes the source scenario before CLI overrides and excludes `policy_override`/`admission_mode`, so paired runs of one trace share it; the arm-specific settings and their hash are in `manifest.execution` (`sha256`). `--require-parity` and `python -m app.benchmarks.parity` reject `unknown` comparison metadata (see `docs/inference-e6-dynamo-comparison.md`).

### Running E5 recompute-control and locality-matrix runs (no KV transfer)

The scenarios `config/scenarios/e5_*.json` are generated by `app.benchmarks.e5_locality` (a test fails if the committed files drift; regenerate with `uv run --project services/app python -m app.benchmarks.e5_locality`). Each turn carries `force_worker` (`worker_a|worker_b`, per turn or per conversation default), sent as `x-force-worker`. The gateway honors it **only** with `ALLOW_FORCED_PLACEMENT=1` (off in the shipped manifest; enable for the experiment only). The replayer verifies every forced turn: the response must show `x-place-decision` equal to the requested worker **and** `x-placement-policy: forced`. A mismatch marks the turn `control_not_applied`; if the first forced response is not honored the run aborts (exit 2, no evidence). A turn shed before placement is an ordinary failure, not a control failure. The manifest records `treatment`, `force_worker_requested`, `force_worker_verified` and `control_observations.force_worker`.

| Scenario | Path | What it is |
|---|---|---|
| `e5_local_reuse_<size>` | A to A | Local reuse is possible; confirm with observed reuse |
| `e5_recompute_control_<size>` | A to B, no transfer | **E5 control**: B must prefill the prefix itself |
| `e5_destination_hit_<size>` | B warmed, then A-origin continuation to B | B hits its own cache; not a hop |
| `e5_local_eviction_4k` | A to A after filler pressure | Needs eviction; the scenario does not prove it happened |

Sizes are `1k|2k|4k|7k` (gateway estimate, chars/4, inside the 8192 context with `max_tokens` 32; a test enforces the budget). They are **SYNTHETIC** padded prefixes with a unique leading tag per case so one case's KV cannot be reused by another. Cite `manifest.json` `system_prefix.exact_tokens`, not the label. Re-running a case against warm workers reuses the earlier cache, so restart the workers (and gateway) between repeats of the control. `x-intended-action` is the router's belief, not proof; observed reuse comes from per-request TTFT and window-level prefix-cache deltas (`--metrics-url`, one worker only). Worker A and B share one physical A100; disclose that.

```bash
make replay-e5 E5_SCENARIO=e5_recompute_control_2k TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18002/metrics
```

The real-transfer treatment is **not implemented** and is blocked on #133; it must reuse exactly these four sizes and conditions. No transfer backend is configured, and a worker change or lower latency is never evidence of a hop.

### Tracing one request end to end

`run_scenario.py` writes `requests.jsonl` (per request). Pull the gateway decision log (JSON lines, `kubectl logs deploy/inference-gateway > gateway.log`; `make inference-pull-evidence` does not currently pull it) and join them:

```bash
make trace-request RUN_DIR=metrics/evidence/<run> REQUEST_ID=<request_id> GATEWAY_LOG=gateway.log
# list ids: uv run --project services/app python services/app/scripts/trace_request.py --run-dir <run> --list
# --json out.json also writes the structured trace; <run>/gateway.log is used when present
```

The timeline covers app, guard, admission, placement, queue, hop, engine, TTFT/post-first-token, tool call and next agent step, with durations where derivable, chosen worker, policy, status, tokens and whether the SLO was met. `post_first_token_ms` is client-observed time from first content to completion (e2e minus TTFT; includes streaming transport and gateway work), **not** vLLM decode; per-request engine prefill/decode timing is unavailable (window-level only). Hop is `not attempted (#133)` unless the gateway log holds a hop record with **every** proof field in `HOP_PROOF_FIELDS` (`app/benchmarks/trace.py`): `source_worker_or_store`, `destination_worker`, `prefix_identity`, `compatibility_namespace`, `transferred_tokens` > 0, `transferred_bytes` > 0, `transfer_ms`, `hop_result: transferred`, `confirm_result: available`, `destination_reused_tokens` > 0. These field names are an **assumption until #133 lands**; a `transferred` record missing any of them is `attempted_not_confirmed` with `missing_proof_fields` listed. The proof is also checked as a relationship against this request's own evidence: source differs from destination, destination equals the chosen worker (gateway placement record or `x-place-decision`), reused tokens do not exceed transferred tokens, `prefix_identity` equals the request's prefix id, and `compatibility_namespace` matches the manifest's revisions (`compatibility_namespace`, or every recorded model/tokenizer/chat-template revision). Contradictions are listed as `failed: ...`; a check whose correlated data is absent is `unverifiable: ...` and also blocks `confirmed`. Engine wait is per-request only as gateway dispatch-to-release; vLLM queue/running values are shown as **window-level** context and never as the request's own. Missing inputs (no gateway log, no `--metrics-url`, no tool record) are listed as unavailable rather than estimated. Stage durations need the `received_at`/`ts` log fields; older gateway logs lack them.

### Scenario CLI Parameters:
- `--scenario`: Scenario name (without `.json`) or path to custom JSON scenario.
- `--target-url`: Target server (`http://localhost:18080` for Gateway, `http://localhost:8000` for App).
- `--metrics-url`: Prometheus metrics endpoint (`http://localhost:18001/metrics` for Worker A).
- `--concurrency`: Override concurrency semaphore limit.
- `--endpoint-type`: `gateway_chat` (direct OpenAI `/v1/chat/completions`) or `app_runs` (FastAPI `/api/runs` with SSE).
- `force_worker` (scenario field): per-turn `x-force-worker` for E5 (gateway needs `ALLOW_FORCED_PLACEMENT=1`).
- `--policy-override` / `--admission-mode`: send the test-only gateway control headers (gateway needs `ALLOW_EXPERIMENT_CONTROLS=1`).
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

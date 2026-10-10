# Inference Cluster & Serving Architecture — Track B Submission

**Repository:** [GitHub: NakulManchanda/ai-analytics-poc](https://github.com/NakulManchanda/ai-analytics-poc)  
**Submission Track:** **Track B — Tool-Using Agent** (CrewAI Multi-Agent Strategy + FastMCP DuckDB Tools)  
**Canonical Design Doc:** [DESIGN.md](file:///Users/nakulmanchanda/dev/ai_app_poc/DESIGN.md)  
**Evidence Artifacts:** [docs/inference-experiments/evidence/](file:///Users/nakulmanchanda/dev/ai_app_poc/docs/inference-experiments/evidence/) (7 plots, 11 curated metric summaries)

---

## Part 0. Why Track B (Tool-Using Agent)

Our application is an enterprise analytics copilot for the NYC Yellow Taxi dataset:
1. **The Loop:** A user query triggers a multi-step agent loop (`think` → `tool call` → `observe` → `answer`).
2. **Framework:** Implemented using **CrewAI** (`Researcher` + `Writer` agents) and standard iterative tool loops interacting with an independent **FastMCP** service executing SQL queries over local Parquet tables in DuckDB.
3. **Prefix & Token Characteristics:**
   - **Shared Prefix:** Global system instructions + tool JSON schemas (fixed **223 exact tokens**, cached across all sessions).
   - **Lengthening Context:** Each conversation turn appends tool observations and agent reasoning tokens, creating lengthening prefills across successive turns.
   - **Traffic Classes:** Interactive single-turn/multi-turn user queries (`tenant_interactive`), synthetic noisy agent bursts (`tenant_noisy`), and background evaluation/sweeps (`tenant_batch`).

---

## Part 5. Queue and Engine Deep-Dive (Answers to Core Prompt Questions)

### 1. Who sits in your queue vs vLLM's waiting queue?
- **Gateway Queue:** Incoming requests wait in the orchestrator's per-worker priority queue (`infra/inference/gateway/queueing.py`). Requests are ordered by priority class (interactive before batch), prompt size, slack time, and arrival age.
- **vLLM's Waiting Queue:** Because the gateway enforces `WORKER_MAX_INFLIGHT = 8` (which exactly matches vLLM's `--max-num-seqs 8`), **vLLM's internal waiting queue stays at 0**. The gateway acts as the admission barrier; the engine never queues work it cannot immediately execute.
- **Measured Evidence (E4):** Prometheus metric `vllm:num_requests_waiting` peaked at `0` on both workers throughout the entire run, while gateway queue wait reached up to 1.25s under burst.

### 2. Waiting / running / swapped (or preempted)?
- Requests waiting: Gateway queue depth reaches up to offered concurrency; vLLM waiting = 0.
- Running: Capped at 8 per worker (16 across the cluster).
- Swapped / Preempted: **0 preemptions** across all experiments. The gateway sheds or queues before the engine is forced to evict running blocks.

### 3. `orch_replica_queue_depth` — which pod, under which mix?
- Exported as Prometheus gauge `orch_replica_queue_depth{worker, class}`.
- Under `least_loaded`, queue depth splits evenly between `worker_a` and `worker_b`.
- Under `prefix_then_load` (E3), queue depth temporarily skews to the worker holding the prefix cache when bursts arrive for that conversation.

### 4. If a 32k RAG retrieve and a short agent decode are both ready, who goes first?
- **The Gateway Decides Entry Order:** The gateway queue sorts by traffic class, then size, slack, and age. Interactive agent decodes jump ahead of long retrieve requests.
- **Safety Boundary:** A 32k prompt is rejected immediately at the door by `gateway/guard.py` (`context_window_exceeded`, HTTP 413) because the engine context window is configured to 8,192 tokens.

### 5. PagedAttention vs Prefix Cache: which saved memory on your shared-prefix mix?
- **PagedAttention** saves physical memory allocation by allocating non-contiguous 16-token physical blocks instead of allocating a worst-case contiguous buffer for each sequence.
- **Prefix Caching** saved **compute (prefill tokens)**, not memory. Memory remains preallocated to the KV block pool (7.61 GiB flat HBM allocation). Prefix caching eliminated ~72% to 98% of prefill computations on shared system prompts and conversation history.

### 6. Chunked prefill & continuous batching: engine flags used and why?
- `--max-num-seqs 8`: Matches the concurrency capacity limit discovered in E0.
- `--max-num-batched-tokens 2048` (reduced from 8,192): Ensures large prefill requests are chunked into 2,048-token batches. This prevents a large prompt from starving ongoing decode tokens of SM compute, while also freeing ~175 MiB of activation memory back into the KV cache pool (4,546 blocks vs 4,450 blocks).
- `--enable-prefix-caching` and `--block-size 16`: Enables prefix block matching.

### 7. KV full after admit: do you shed at the door, or does the engine preempt?
- **Two-tier protection:** The gateway sheds at the door with HTTP 503 `kv_pressure` if worker free KV drops below 10%. As a last resort, vLLM would preempt running sequences. In practice, KV peaked at 46%, so preemption was never triggered.

### 8. Client gone (aborted): who frees the KV? And how?
- The gateway listens for client disconnects over the HTTP stream (`request.is_disconnected()`), immediately cancels the async generator, and terminates the upstream httpx connection to vLLM. vLLM detects disconnect on the socket and frees the associated KV blocks back to the allocator.

### 9. After a worker returns: slam it at 100%, or ramp while p99 holds?
- **Ramped:** The gateway (`infra/inference/gateway/workers.py`) declares a worker warm only after 3 successful metric scrapes and 1 successful probe completion. Once warm, traffic is ramped gradually: max 2 in-flight, then 4, then 8 (30-second ramp steps) while verifying p99 latency holds.

---

## Part 8. Walkthrough & 13 Grading Questions (Where to Point)

| # | Grading Question | Exact Answer | Where to Point (File / Metric) |
|---|---|---|---|
| **1** | What is the app; shared vs unique tokens? | Track B Tool-Using Agent. System instructions and DuckDB tool schemas are shared (**223 exact tokens**). Follow-up conversation turns and table results are unique. | `services/app/app/query_catalogue.py`, `docs/prefix-contract.md`, E3 `manifest.json`. |
| **2** | What dies at guard vs admit vs place vs queue? | **Guard:** Malformed JSON (400), token length > 8,192 (413).<br>**Admit:** `tenant_concurrency` (429), `decode_slots` (503).<br>**Place:** `batch_slot_cap` (503).<br>**Queue:** `timeout_queue` (504). | `gateway/guard.py`, `gateway/admission.py`, `gateway/placement.py`, `gateway/queueing.py`. Metrics: `guard_reject_total`, `orch_shed_total`. |
| **3** | Where do I prevent work that will time out? | Admission checks `deadline_unachievable` before entry; queue discards requests whose wait time exceeds remaining deadline (`timeout_queue`). | `gateway/admission.py` (`should_admit`), `gateway/queueing.py`. Metric: `queue_error_total{reason="timeout_queue"}`. |
| **4** | Where do I protect KV? | Admission sheds on `kv_pressure` (< 10% free blocks); router skips low-KV workers; guard enforces token budget window. | `gateway/admission.py`, `gateway/placement.py`. Metric: `orch_shed_total{reason="kv_pressure"}`. |
| **5** | Where do I prioritize interactive traffic? | Gateway queue dispatch key evaluates priority class first (`interactive` before `batch`); batch is capped at 6 of 8 slots per worker. | `gateway/queueing.py` (`_entry_sort_key`). E4 plot: `docs/inference-experiments/evidence/plots/e4-gateway-queue-wait-*.png`. |
| **6** | Where do I stop one tenant owning the GPU? | Per-tenant token budget (200k/window) and concurrency limiter (`TENANT_MAX_CONCURRENCY=10`). Rejected with 429 and **never overflowed**. | `gateway/tenants.py`. Counters: `orch_shed_total{code="429", reason="tenant_concurrency"}`. |
| **7** | Where do I hop, what is not copied? | Hop moves KV across workers via LMCache + Mooncake over TCP when placement selects a different worker. Request bodies, weights, and engine state are **not** copied—only compatible KV block tensors. | `infra/inference/gateway/hop.py`, `infra/inference/kv_transfer/evidence.py`. |
| **8** | Where do I evict, what becomes a ghost? | Eviction is managed by the backing store (Mooncake/LMCache) and vLLM LRU. A "ghost" is a directory entry referencing evicted blocks. Avoided by requiring positive bytes transferred and post-forward consumption before confirming. | `infra/inference/kv_transfer/` contract tests: `test_kv_transfer_contract.py`. |
| **9** | Where does engine scheduler sit vs admit/place/queue? | Gateway admits, places, and queues. Engine (vLLM) only handles execution of dispatched requests (prefill, decode, PagedAttention block allocation). Gateway caps inflight at `--max-num-seqs` so vLLM scheduler stays in continuous execution. | `services/app/scripts/trace_request.py` lifecycle stages 1 through 10. |
| **10** | What limited concurrency on this GPU for this app? | **The 8-slot engine scheduler cap (`--max-num-seqs 8`).** Concurrency peaked at 12; KV memory peaked at only 46%, proving decode slots are the bottleneck. | E0 summary: `docs/inference-experiments/evidence/metrics/e0-capacity_summary-*.json`, plot `e0-slots-running-waiting-*.png`. |
| **11** | Four production alerts? | 1. `InferenceKVPressureSustained` (KV > 85% for 5m)<br>2. `GatewayInteractiveTTFTSLOBreach` (p95 TTFT > 1.5s)<br>3. `GatewayQueueShedSurge` (shed rate > 5 req/s)<br>4. `InferenceWorkerIntegrity` (scrape failure / replica down). | `infra/inference/observability/prometheus/alerts.yaml`. |
| **12** | If I scale, which pool? | **Decode slots pool.** E0 proved KV is cold and decode slots are hot. Slicing another colocated replica on the same GPU does not add compute—scaling requires adding decode-optimized GPU nodes. | `DESIGN.md` Section 3 & 7. |
| **13** | What changes at 10x, and which 3 knobs are the wrong next move? | **At 10x:** Split prefill/decode nodes, add external KV store (Mooncake cluster), autoscale on gateway queue depth.<br>**Three Wrong Knobs:**<br>1. *Raising `--max-num-seqs` blindly* (saturates SM compute).<br>2. *Raising `--max-model-len`* (shrinks KV pool per paper math).<br>3. *Adding identical colocated replicas on the same SMs* (duplicates weights without adding compute). | `DESIGN.md` Section 8. |

---

## Part 9. Mistakes Avoided & Boundaries Defended

- **No benchmark batch size quoted as an SLO:** SLOs are based on end-to-end interactive response requirements (1.5s interactive, 30s batch), not synthetic batch benchmarks.
- **No "add a replica" without naming the limiter:** Colocated replicas share SMs; scaling must target decode compute.
- **No NCCL/NIXL in control plane:** Hop uses LMCache with Mooncake over TCP.
- **A replica is warm after probes, not when weights load:** Requires 3 healthy scrapes and a completed probe request.
- **Named overflow model:** Specifically configured for `superlinked/Qwen/Qwen3.8-27B-FP8`.
- **A 429 never overflows:** Tenant quota rejections strictly stay local.
- **The gateway does not "fix OOM":** Memory is preallocated and guarded; `slice_oom` stays local.
- **Honest reporting on E5 and live proofs:** We explicitly document that live overflow and real-app CrewAI /serve were verified in `d123-20261010-e4-0246`, while live KV crossover (E5) was implemented and contract-tested but not run live.

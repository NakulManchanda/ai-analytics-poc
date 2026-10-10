# DESIGN: an inference cluster for a tool-using taxi-analytics agent

Draft 2026-10-10. Numbers are tagged **measured** (from a retained run), **computed** (arithmetic from measured inputs) or **inferred**
(my reading, not tested). Evidence lives under `metrics/inference/<run id>/` (gitignored; the small files this document cites are
copied to the submission folders listed in section 12). Paths are relative to the repository root.

## 0. Where this stands (read first)

| Part of the brief | Status |
|---|---|
| App wired to the serve path (guard, admit, place, queue, engine, overflow) | built; serve smoke passes on the live cluster (headers, one tool call, two guard rejects) |
| Live `/metrics` and Grafana on a real A100, two workers | **done**: E0-E4 ran on an NVIDIA A100-SXM4-40GB, two HAMi slices |
| Capacity on paper and the first limiter | **done**: hypothesis (KV first) was wrong; the scheduler slot cap is first (E0) |
| Warmup proof | **done** for the engine (E1, E2); declared-warm gate built and observed once (`warm_probe_total`), not yet measured as a cold-to-warm timing |
| Guard, admit, place, queue decisions | built and unit-tested; E3 (placement) and E4 (admission on/off) ran live; **E4 result is neutral to negative** (section 7) |
| KV hop (LMCache + Mooncake, TCP) | **built, merged and tested; NOT run live**, so there is no hop measurement in this submission |
| Overflow to a hosted model | built and unit-tested; destination verified with a live check (chat, tools, streaming); the overflow path itself has **not** fired in a live run |
| Demo window that fills every dashboard | **not done** (guard, tenant 429, `decode_slots` 503 and queueing appear in E4; deadline 504, queue timeout, overflow and hop do not) |

## 1. The application (Part 0)

**Track B: a tool-using agent.** A "NYC taxi analytics" assistant: a user turn starts a loop of think, call a tool (DuckDB queries through an MCP
server over a pinned dataset schema), observe, answer. Context grows every step. Code: `services/app/app/` (`ServeLLMClient` routes every model call
through the gateway `/serve`; anti-bypass tests in `services/app/tests/test_gateway_bypass.py`), MCP service in `services/mcp/`.

**Shared vs unique tokens** (`docs/prefix-contract.md`, `services/app/app/benchmarks/canonical_prefix.py`):
- *Global shared*: system prompt, tool schemas, pinned dataset schema. **223 tokens** (measured, `system_prefix.exact_tokens` in the E3 manifest).
- *Conversation shared*: earlier turns and tool outputs; grows every step and is the reusable prefix across agent steps.
- *Unique*: the newest user turn or observation.
- `prefix_id` = SHA-256 of the global plus conversation regions (first 16 characters); it advances when a tool observation is appended.

**Request classes.** Interactive (the user turn) and batch (background sweeps), carried as `x-request-priority`; tenants carried as `x-tenant-id`.

**Traffic used as evidence.** E0-E4 replayed taxi-shaped scenarios (`config/scenarios/`) through the gateway with `services/app/scripts/run_scenario.py`.
They are generated from the real prefix and tool material, not anonymous text. The real app loop through `/serve` was not part of a retained run.

## 2. Capacity on paper (Part 1)

- **GPU:** one NVIDIA A100-SXM4-40GB (40,960 MiB), split by HAMi into two 50 percent slices, one per worker (20,480 MiB each inside the pod).
- **Model:** Qwen/Qwen3-0.6B, bfloat16, 28 layers, 8 KV heads, head dim 128.
- **KV bytes per token:** 2 x 28 x 8 x 128 x 2 = **114,688 bytes (112 KiB)** (computed; the capacity runner prints the same).
- **KV pool per worker:** 7.61 GiB = **71,200 tokens** (vLLM startup log, E0/E1 configuration with `--max-num-batched-tokens 8192`). After changing the flag to 2048
  the same workers report 4,546 blocks x 16 = **72,736 tokens** (measured, startup scrape); why the pool grew is not yet confirmed.
- `max_concurrent_seqs ~ pool / (kv_bytes_per_token x length)` in tokens: at `max_len` 8,192 -> 71,200 / 8,192 = **8.69**; at the app's measured lengths
  (p50 about 541 tokens, max about 984; from E3 `tokens_in` + `tokens_out`) -> about **130** at p50 and about 71 at the maximum. (computed)
- **Engine caps:** `--max-num-seqs 8`, `--max-model-len 8192`, `--gpu-memory-utilization 0.45`, prefix caching on, chunked prefill on.
  8 x 8,192 = 65,536 < 71,200, so running sequences alone cannot fill the KV pool. (computed)
- **Hypothesis and outcome.** I expected KV to run out first. It did not. The 8-slot scheduler cap is first at every context length except 8,192, where the
  batched-token limit takes over; KV peaked at 46 percent in the direct capacity run and 3 percent in the gateway sweep (E0, measured). Zero preemptions, zero errors.
- **Model switch (to be written from the model configs, not from memory):** a larger model or FP8 KV changes bytes per token and therefore the 71,200-token pool;
  not measured in this submission.

## 3. The cluster (Part 2)

| Decision | Choice and reason |
|---|---|
| GPU | A100-SXM4-40GB: enough HBM for two slices of a small model, and the course reference. A smaller card would not fit the 8,192-token paper math with two workers. |
| Model | Qwen3-0.6B: tool calling works with the hermes parser, weights are small against a 20 GiB slice, and the app's contexts are short (p50 about 541 tokens). The cost is that KV is never the limiter. |
| Topology | Two colocated replicas on one GPU, no prefill/decode split. Both share the SMs, so a second replica adds slots but not compute; weights and KV are duplicated. HAMi 50/50 memory and core slices. |
| Engine box | vLLM v0.11.0, two Deployments (`infra/inference/k8s/workers/`), `ClusterIP` only, no public port. |
| Gateway box | One FastAPI Deployment (`infra/inference/gateway/`, manifest `infra/inference/k8s/gateway/gateway.yaml`): guard, admit, place, queue, hop decision, overflow. The gateway never loads a model. |
| Concurrency | `--max-num-seqs 8`, `--max-model-len 8192`; gateway `MAX_DECODE_SLOTS=8`, `WORKER_MAX_INFLIGHT=8` (a test ties them to the engine flags). |
| Hop backend | LMCache with Mooncake over TCP (`infra/inference/kv_transfer/`, `infra/inference/mooncake/`). Built and tested; not run live. |
| Overflow | A hosted OpenAI-compatible endpoint serving the same family at larger size: Superlinked, `Qwen/Qwen3.8-27B-FP8`. Only a 503 or 529 whose reason names a capacity limiter (`decode_slots`, `kv_pressure`, `no_signal`) may leave; 429, 500 and `slice_oom` stay local. |
| Scaling | **Nothing scales** (two fixed replicas, no KEDA). The rule if it did: hot decode slots with low KV and low uncached prefill -> decode capacity; high uncached prefill tokens -> prefill capacity. E0 says decode slots are hot and KV is low, so decode is the pool, with the caveat that a colocated replica shares SMs and duplicates weights and KV. Not "add another replica of the same size". |

**Network.** The gateway, workers and Prometheus/Grafana are reached through a loopback-only SSH tunnel (`make inference-tunnel`); a smoke check confirms the worker ports are not reachable without it.

## 4. The control plane (Parts 3, 4, 7)

Order in the handler (`infra/inference/gateway/main.py`): guard -> tenant quota -> admission -> placement -> queue -> hop -> engine -> overflow gate. Every response carries stage headers
(`x-guard-decision`, `x-admit-decision`, `x-place-decision`, `x-queue-decision`, `x-queue-wait-ms`) so a request's path can be read back from `requests.jsonl`.

| Stage | Decides | Reasons (metric label values) | Code |
|---|---|---|---|
| Guard | first no; never reaches a GPU | `malformed_payload`, `missing_messages` (400), `prompt_too_long`, `context_window_exceeded` (413); prompt + `max_tokens` is checked against `MAX_MODEL_LEN`; a caller header can only raise the token estimate | `gateway/guard.py` |
| Tenant quota | one tenant cannot own the GPU | 429 `tenant_tokens`, `tenant_concurrency` (never overflowed); cap is per allowlisted tenant, `TENANT_MAX_CONCURRENCY=10`, budget 200,000 tokens per window | `gateway/tenants.py` |
| Admit | should we accept it | 503 `no_signal`, `kv_pressure`, `decode_slots` (batch gets `MAX_DECODE_SLOTS` minus a 25 percent reserve = 6 per worker); 504 `deadline_unachievable` | `gateway/admission.py` |
| Place | which worker | policies `least_loaded`, `p2c`, `prefix_then_load`; sticky KV owner moved only when saturated, unhealthy or cold; anti-herding pending count; errors `no_healthy_worker`, `batch_slot_cap`, `unknown_policy` | `gateway/placement.py` |
| Queue | what runs next at entry | per-worker queue ordered by class, long-prompt flag (>= 2,000 tokens), slack, age; anti-starvation (`MAX_OVERTAKES` 8); waiting limited to the smaller of the queue timeout and the remaining deadline -> `timeout_queue`, `queue_full` | `gateway/queueing.py` |
| Warm | declare a worker warm | N healthy scrapes (3) plus one successful probe request; a returning worker is capped and ramped (2 then 4 in-flight, 30 s steps) | `gateway/workers.py` |
| Hop | move KV or recompute | same worker = no-op; different worker only when the reusable prefix is at least a configured minimum and enough deadline remains | `gateway/hop.py` |
| Overflow | stay or leave | above | `gateway/overflow.py` |

**The engine is not reimplemented.** vLLM owns waiting/running/preempt, the block table, chunked prefill and kernels. The gateway only orders entry and caps dispatch per worker.

## 5. Queue and engine (Part 5)

- **Who sits where.** Requests wait in the gateway's per-worker queue (`orch_replica_queue_depth{class,worker}`). Because `WORKER_MAX_INFLIGHT` equals `--max-num-seqs`, vLLM's own waiting queue stays empty.
  Measured in E4: vLLM mean queue time about 23 microseconds, `vllm:num_requests_waiting` maximum 0 on both workers, while gateway queue wait reached about 1.2 s.
- **A 32k retrieve and a short agent decode both ready:** the gateway decides entry order (class, then size, slack, age); the engine decides execution order once dispatched. (A 32k prompt is rejected by the guard at this 8,192-token window.)
- **PagedAttention vs prefix cache.** Paging lets a 8,192-token request cost 8,192 / 16 blocks instead of a worst-case reservation; the prefix cache saved prefill, not memory. Measured prefix-cache hit rate 72-90 percent on the taxi trace (E3, E4). Memory is dominated by the preallocated pool, so flat HBM is normal.
- **Flags and why.** `--max-num-seqs 8` (the scheduler limit E0 found), `--max-num-batched-tokens 2048` (changed from 8,192 so long prompts are chunked), `--enable-prefix-caching`, `--block-size 16`, chunked prefill on.
  Whether a real long prompt gets chunked at 2,048 has not been confirmed from a request.
- **KV full after admit.** Admission sheds `kv_pressure` below 10 percent free KV; vLLM preempts as a last resort. Neither fired (KV never above 46 percent), so this is unexercised.
- **Client abort.** Gateway closes the upstream stream; vLLM aborts on disconnect and frees the KV. The cancel path is unit-tested; the KV drop was not scraped.
- **A returning worker** is ramped, not slammed: cap 2, then 4, raised every 30 s while the worker has no waiting requests and enough free KV.

## 6. Hop and warm (Part 6)

- **What "hop" means here:** a conversation moves to a different worker by placement; no prefill/decode split. When it does, the gateway either recomputes or asks the destination to load the prefix from the shared store (LMCache + Mooncake, TCP).
- **Not copied:** weights, request bodies, generated output, scheduler state; only KV blocks for identical token IDs under a matching compatibility namespace.
- **Proof contract (built):** a hop is `confirmed` only with positive transferred tokens and bytes, source != destination, matching prefix identity and namespace, and the destination consuming the blocks after its forward pass (`infra/inference/kv_transfer/evidence.py`, `validate_run`). A worker header or lower latency is not proof.
- **Warm.** A replica is not ready when the weights are on the GPU: it is warm after the scrape-plus-probe gate. Engine-side TTFT after a restart is 5-20 ms; the first request after restart was slower in 1 of 6 and not at all after a 300 s idle gap (E1, measured). Client numbers of 250-350 ms are the SSH tunnel, not the GPU. A restarted worker recovers in about 95-135 s.
- **Status:** the live hop run (E5) was not done; no crossover is claimed. The control legs (recompute, local reuse, destination hit) were not run either.

## 7. What the experiments showed

| # | Question | Result (measured) |
|---|---|---|
| E0 | what runs out first | The 8-slot scheduler cap. Gateway sweep raw throughput peaks at about concurrency 12 (5.92 req/s, 757 tok/s). KV 3-46 percent. Goodput is 0 (tunnel). |
| E1 | cost of a cold worker | Engine first request a few ms slower at most; none after 300 s idle; restart recovery about 95 s. |
| E2 | warm vs cold prefix cache | Turn-1 TTFT p50 2,132 ms cold vs 681 ms reused (about 3x); later turns no benefit (the cold run already reuses its own prefixes). |
| E3 | `least_loaded` vs `prefix_then_load` | Prefix-aware: later turns faster (turn 3 TTFT p50 467 vs 876 ms), first turn slower (2.70 vs 1.80 s), p95 worse (2,702 vs 1,796 ms). A trade-off, not a win. Follow-up turns lost affinity (`prefix_overlap_low`); fixed afterwards by the sticky-owner change, not re-measured. |
| E4 | admission on vs off | Neutral to negative (below). |

**E4 (run `d123-20261010-e4-0003`, 22 conversations, 48 turns, tenant cap 10).**
| | OFF | ON |
|---|---|---|
| Completed turns | 32 of 48 | 20 of 48 |
| `tenant_interactive` completed | 12 of 12 | 8 of 12 |
| Failures | 12 x 503 `batch_slot_cap`, 4 x 429 `tenant_concurrency` | 24 x 503 `decode_slots` (12 batch, 8 noisy, 4 interactive), 4 x 429 |
| `tenant_interactive` TTFT p95 / gateway queue wait p95 | 1,048 ms / 540 ms | 638 ms / 0 ms |
| `timeout_queue`, `deadline_unachievable` | 0, 0 | 0, 0 |

- Admission ON did not serve more interactive traffic: it traded completions for tighter tails. The OFF arm never overloaded the engine, so the shed requests would likely have succeeded within their deadlines (inferred).
- Two protections survive `x-admission-mode: off`: the tenant cap and the batch slot cap at placement. The batch tenant is starved in both arms.
- `deadline_unachievable` cannot fire in this setup: its estimate uses vLLM `waiting`, which is always 0 because the gateway holds the queue.
- Pasted scrape (gateway counters after the pair; the first four lines include two earlier cap tests, subtract `gateway-counters-start.txt`):
```
orch_shed_total{class="interactive",code="429",reason="tenant_concurrency"} 12.0
orch_shed_total{class="batch",code="503",reason="decode_slots"} 12.0
orch_shed_total{class="interactive",code="503",reason="decode_slots"} 12.0
orch_tenant_total{outcome="admitted",tenant="tenant_noisy"} 52.0
orch_queue_wait_seconds_count{class="interactive",worker="worker_b"} 37.0
orch_queue_wait_seconds_count{class="interactive",worker="worker_a"} 35.0
```
- Measured over the E4 window (Prometheus): effective prefill about 20,280 tokens/s (A 19,301, B 21,174; includes prefix-cache hits, an upper bound on the uncached rate), vLLM queue time about 0.000023 s, `num_requests_waiting` maximum 0.

## 8. The questions, with where to point (Part 8)

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | What is the app; shared vs unique tokens? | section 1 | `docs/prefix-contract.md`; E3 `manifest.json` `system_prefix.exact_tokens` = 223 |
| 2 | What dies at guard vs admit vs place vs queue? | section 4 reasons; E4 shows tenant 429, `decode_slots`, `batch_slot_cap` | `guard_reject_total{reason}`, `orch_shed_total{reason,class,code}`, `queue_error_total{reason}`; E4 `requests.jsonl` |
| 3 | Where do I prevent work that will time out? | admission `deadline_unachievable` and the queue's deadline-bounded wait | code and unit tests; **never fired live** (see section 7) |
| 4 | Where do I protect KV? | `kv_pressure` shed, placement skips low-KV workers, guard window check, engine preemption | **never exercised** (KV at most 46 percent) |
| 5 | Where do I prioritize interactive? | queue dispatch key puts class first; batch limited to 6 of 8 slots | `orch_queue_wait_seconds{class}`; E4: interactive p95 queue wait 0 vs 540 ms |
| 6 | Where do I stop one tenant owning the GPU? | per-tenant concurrency and token budget, 429, never overflowed | E4: `tenant_noisy` 4 x 429 per arm |
| 7 | Where do I hop, what is not copied? | section 6 | built; **no live hop evidence** |
| 8 | Where do I evict, what becomes a ghost? | eviction lives in the store (Mooncake/LMCache) and vLLM; a ghost is a directory entry for blocks that are gone; the worker requires positive bytes and post-forward consumption before `confirmed`; the gateway's own prefix belief is time-bounded. The `MetadataDirectory` class is a tested library, not on the live path, and no eviction counters are exported by the gateway. | `infra/inference/README.md` "Eviction and ghost entries"; unit tests in `infra/inference/tests/test_kv_transfer_contract.py` |
| 9 | Where does the engine scheduler sit vs my admit/place/queue? | section 4 and 5 | `make trace-request` timeline |
| 10 | What limited concurrency? | the 8-slot scheduler cap | E0 `capacity_summary.json`, `sweep.csv`, `prometheus_range/` |
| 11 | Four production alerts | `InferenceKVPressureSustained` (KV > 85 percent for 5 m), `GatewayInteractiveTTFTSLOBreach`, `GatewayQueueShedSurge`, `InferenceWorkerIntegrity` | `infra/inference/observability/prometheus/alerts.yaml`; thresholds uncalibrated; none fired in E0-E4 |
| 12 | If I scale, which pool? | decode slots (section 3) | E0 |
| 13 | What changes at 10x, and which three knobs are the wrong next move? | below | plan section on 10x |

**At 10x traffic:** separate interactive and batch admission budgets, stronger prefix-aware routing (with a load guard on bursts, per E3), external KV, autoscaling on queue depth, decode-slot and prefill signals, and stricter tenant and deadline policy.
**Three wrong next moves:** (1) raising `--max-num-seqs` blindly: it is the limiter, but compute is shared on one GPU, so test it with a measurement; (2) raising `--max-model-len`: it shrinks concurrency per the paper math; (3) adding an identical replica without naming the limiter: it duplicates weights and KV on the same SMs.

## 9. Mistakes avoided (the brief's list)
No benchmark batch size is quoted as an SLO; no "add a replica" without a limiter; NCCL/NIXL are not used (the hop backend is LMCache + Mooncake over TCP); a replica is warm after a probe, not when weights load;
overflow names its model and its limiter; a 429 never overflows; the gateway protects but does not "fix OOM" (`slice_oom` is detected and stays local); RAG is not a third phase; cold-replica TTFT is not quoted as an SLO
(the 100 ms SLO is not met through the tunnel, so goodput is reported as 0 and not used).

## 10. Limits and what I did not do
- **No live KV hop, no recompute-vs-hop crossover, no control legs.** The hop is implemented, merged, and covered by unit and contract tests only.
- **Overflow never fired live.** The destination was checked (models, chat, tools, streaming) but no request overflowed in a run.
- **Client latency goes through an SSH tunnel**, about 300 ms or more per request, so goodput against the 100 ms SLO is 0 in every run and is not used.
- **One run per arm**, no confidence intervals; E3 predates the sticky-owner placement fix.
- **Admission thresholds are partly placeholders** (`PREFILL_TOKENS_PER_S` 4,000, `QUEUE_WAIT_PER_WAITING_S` 0.25); measured values are recorded in `docs/inference-experiments/` but not applied.
- **No scaling and no replicas panel**; the A/B on larger models and FP8 KV is paper-only.
- **Eviction** is described, not demonstrated.

## 11. Reproducing
`make inference-fresh-up`, `make inference-tunnel`, then the ordered commands in `docs/inference-experiments/inference-run-playbook.md` ("Start here", then E0-E4). The meaning of each experiment is in `docs/inference-experiments/inference-experiments-reference.md`.

## 12. Repository map
| Brief name | Path |
|---|---|
| `app/` | `services/app/app/` (`ServeLLMClient`, agent loop), `services/mcp/` |
| `control/` | `infra/inference/gateway/` (guard, admit, place, queue, overflow, hop), `infra/inference/kv_transfer/` |
| `cluster/` | `infra/inference/k8s/`, `infra/inference/scripts/`, `infra/inference/mooncake/` |
| `notebook/` | `experiments/123_evidence.ipynb` (generated by `make evidence-notebook`); an executed copy with E0-E3 outputs was rendered on 2026-10-05; E4 is not in it yet |
| `plots/`, `metrics/` | `docs/inference-experiments/evidence/` (to be populated with the small tracked files this document cites) |
| tests | `infra/inference/tests/`, `tests/inference/`, `tests/experiments/` |

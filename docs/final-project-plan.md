**Final Project Plan  
NYC Taxi Analytics Agent on a GPU-Aware Inference Cluster**

*Track B - Tool-using agent | Control plane + vLLM workers + KV-aware routing + hop store + observability*

**Primary thesis: prove that admission, placement, queueing, prefix/KV locality, and hop decisions improve a real multi-step agent workload under constrained GPU memory.**

# 1. Project thesis and success criteria

- Application: NYC Taxi Analytics agent. A user question triggers a ReAct-style loop: model -> DuckDB/MCP tool -> observation -> model -> final answer.
- Serving shape: shared system/tool prefix, conversation-specific growing prefix, unique newest suffix, repeated inference steps inside one user turn.
- Control-plane ownership: guard -> admit/shed -> place -> gateway queue -> hop check -> vLLM. vLLM still owns engine waiting/running/preemption/chunked prefill/continuous batching.
- Primary experiment: least-loaded routing vs prefix/KV-aware routing under the same traffic trace.
- Supporting experiments: prefix reuse on/off or cold/warm; no-admission vs protected admission; recompute vs KV hop; cold worker vs declared-warm worker.
- Proof standard: every design choice must have a file, scrape, notebook cell, or Grafana panel that shows what happened.

# 2. Target architecture

```text
Taxi Analytics App
      |
      v
Gateway / Control Plane :8080
  guard.inspect()
  should_shed(req, snap)
  place.pick(policy)
  per-worker gateway queue
  overflow gate
      |
      +----------------------+----------------------+
      |                                             |
      v                                             v
Worker A / vLLM :8001                      Worker B / vLLM :8002
  waiting / running / preempt                waiting / running / preempt
  local KV cache                             local KV cache
      |                                             |
      +-------------- hop check --------------------+
                       |
                       v
             LMCache / Mooncake hop store
             transfer / lookup / evict / metadata

Prometheus scrapes: app + gateway + router + queues + workers + hop + overflow
Grafana: overview, gateway, router, queues, vLLM, KV/hop, overflow, GPU/DCGM
Notebook: controlled experiments + pasted scrapes + derived goodput/latency charts
```

**Hardware starting point:** two vLLM replicas on one physical GPU using a 50/50 HAMi-style slice if feasible. Measure KV pressure. If one replica becomes the dominant KV consumer or contention distorts the experiment, move Worker B to a second GPU and document why.

# 3. Repository structure

```text
app/
  api.py
  agent.py
  prompts.py
  tools/duckdb.py
  workloads.py

control/
  guard.py
  admission.py
  placement.py
  queue.py
  prefix_registry.py
  hop.py
  overflow.py
  snapshot.py

cluster/
  vllm-worker-a.yaml
  vllm-worker-b.yaml
  hami/
  lmcache/
  mooncake/
  prometheus/
  grafana/

observability/
  metrics.py
  dashboards.py
  alerts.py

experiments/
  generate_traffic.py
  run_prefix_reuse.py
  run_routing_ab.py
  run_admission_ab.py
  run_hop_ab.py
  run_warmup.py

metrics/
  scrapes/
  run-manifests/

plots/
notebook/final_project.ipynb
DESIGN.md
README.md
```

# 4. Stack choices and why

| Layer | Choice | Reason / what must be proven |
|---|---|---|
| App | FastAPI + existing taxi agent + DuckDB/MCP tools | Real multi-step agent traffic; every LLM call goes through /serve. |
| Gateway | Python/FastAPI control plane | Own guard, admit, place, queue, overflow, metrics. |
| Engine | vLLM first | Existing lab familiarity; live engine metrics; do not reimplement scheduler. |
| Alternative router/control | NVIDIA Dynamo comparison | Compare built-in KV-aware routing against our simpler prefix_then_load policy. |
| GPU slicing | HAMi-style split if one physical GPU | Start 50/50, then justify adjustment from observed KV/latency. |
| KV hop/store | LMCache -> Mooncake | Externalize/share KV metadata/state and measure hop vs recompute. |
| Metrics | Prometheus + Grafana + DCGM exporter | Explain request decisions, engine state, KV, GPU memory/power, goodput. |
| Notebook | Jupyter | Part 5 proof: scrapes, queues, latency/goodput, crossover analysis. |

# 5. Prefix design: shared vs unique tokens

| Prompt region | Reuse scope | Examples | Why it matters |
|---|---|---|---|
| Global shared prefix | Across users | System prompt, taxi dataset rules, tool/MCP schemas | Good candidate for prefix cache reuse across many turns. |
| Conversation prefix | Within one conversation | Prior user messages, tool calls, DuckDB observations | Becomes increasingly valuable on later ReAct steps. |
| Unique suffix | Per inference step | Newest question, newest tool result, latest reasoning/answer suffix | Must be prefetched/decoded anew. |

**Routing implication:** workers are not interchangeable if their KV caches contain different useful prefixes. Placement should trade cache locality against queue/decode pressure rather than blindly choose the least-loaded worker.

# 6. KV capacity math

**General formula**

```text
KV bytes/token = 2 (K+V) x num_layers x num_kv_heads x head_dim x bytes_per_element
```

**Current candidate example - Qwen3-0.6B, BF16 KV:** 28 layers, 8 KV heads, head_dim 128, 2 bytes/element -> 114,688 bytes/token = 112 KiB/token.

| Sequence length | Approx KV / full sequence |
|---|---:|
| 4K | 448 MiB |
| 8K | 896 MiB |
| 16K | 1.75 GiB |
| 32K | 3.5 GiB |

**Per-worker capacity**

```text
KV budget/worker = allocated GPU HBM
                 - model weights
                 - CUDA graphs / activations / runtime reserve
                 - safety headroom

max concurrent sequences ~= KV budget / (KV bytes/token x sequence_length)
```

Report both:
- Capacity at configured max_model_len.
- Capacity at measured taxi-app p50 and p95 context lengths.
- Configured max_num_seqs, which may be lower than the KV-only ceiling because compute/latency/SLO can limit first.

**Important correction for slides/whiteboard examples:** 36,864 bytes/token is about 36 KB/token, not 36.8 bytes.

# 7. Policies by layer

| Layer | Decision | Initial policy | Metric / evidence |
|---|---|---|---|
| Guard | Should this payload ever touch a GPU? | Reject malformed input, prompt injection signatures, unsupported shape, prompt > configured token limit. | guard_reject_total{reason} |
| Admission | Should the cluster accept now? | Shed on tenant_tokens, timeout_queue, low kv_free, decode-slot pressure. | orch_shed_total{reason}, kv_free_ratio, tokens_in_flight |
| Placement | Which worker? | A/B policies: least_loaded vs prefix_then_load; queue depth must be part of score. | orch_pick_total{policy,worker}, estimated_reused_tokens |
| Gateway queue | What is released next? | Interactive priority over batch within deadline; do not recreate vLLM scheduler. | orch_replica_queue_depth, queue_wait_seconds |
| Hop | Does destination already have needed KV? | Same worker: no hop. Different worker: reuse/transfer if beneficial, otherwise recompute. | hop_total, hop_tokens, hop_seconds |
| Warm declaration | Is a worker actually ready? | Weights loaded is insufficient; smoke + warmup + TTFT threshold before full traffic. | warmup_seconds, cold/warm TTFT |
| Overflow | May this leave local cluster? | Only selected capacity failures such as 503/529. 429, app 500, slice/OOM stay local. | orch_overflow_total{reason,destination} |

# 8. Experiment matrix

| Experiment | Control vs treatment | Workload | Primary outputs | Question answered |
|---|---|---|---|---|
| E0 Capacity + first limiter | Paper prediction vs real saturation | Taxi prompts at increasing concurrency/context | KV utilization, waiting/running, preemptions, OOM/shed point | What actually limits this GPU? |
| E1 Warmup | Cold replica vs declared-warm replica | Same fixed taxi request | TTFT p50/p95, warmup duration | When is a replica really ready? |
| E2 Prefix reuse | Cold/non-reused prefix vs reused shared prefix | Repeated system/tool prefix + multi-step conversations | TTFT, computed prefill tokens, cache hit/reuse, KV usage | Does prefix reuse matter for this app? |
| E3 Routing headline | least_loaded vs prefix_then_load | Mixed multi-step conversations with contention | TTFT p95/p99, queue wait, reused tokens, deadline success | When should a request follow its KV? |
| E4 Admission | Naive accept vs should_shed | Interactive + batch + one noisy tenant | Goodput, p99, shed reasons, queue timeout, fairness | Does admission preserve useful work under overload? |
| E5 Hop | Recompute on destination vs LMCache/Mooncake hop | Cross-worker continuation at 2K/8K/16K/32K prefixes | Hop ms/bytes, TTFT, tokens recomputed, crossover point | When is moving KV cheaper than rebuilding it? |
| E6 Dynamo comparison | Our prefix_then_load vs Dynamo KV-aware routing | Same trace and engine/model flags | TTFT/goodput, routing distribution, cache reuse | How much does an engine-informed router improve over our approximation? |

# 9. Throughput, latency, goodput, and orchestration timing

**Measure both raw throughput and goodput.** Throughput can increase while user-visible SLO quality collapses; goodput counts only useful completions that meet the defined SLO.

```text
request good =
  success
  AND TTFT <= interactive_TTFT_SLO
  AND end_to_end <= request_deadline
```

Track:
- requests/sec and tokens/sec
- good requests/sec and good tokens/sec
- TTFT p50/p95/p99
- end-to-end request duration p50/p95/p99
- queue wait
- placement duration
- hop duration
- engine wait
- tool duration
- whole agent-turn duration

# 10. Memory behavior

GPU/KV memory should move up and down with the workload:
- Prefill and newly active sequences allocate/grow KV.
- Decode keeps existing KV hot and extends it token by token.
- Completion, abort, eviction, or preemption should release/reclaim resources.
- Flat-at-max or monotonically growing HBM is suspicious and may indicate retention/ghost state/leaks.
- Correlate HBM with active sequences, prompt length, prefix reuse, queue depth, preemptions, and hop/eviction activity.

Show a time-series panel with:
- DCGM GPU memory used/free
- vLLM KV cache usage
- running/waiting sequences
- request rate
- preemptions
- hop/eviction events

# 11. Overflow policy

| Local result / condition | Stay or leave? | Overflow? | Rationale |
|---|---|---|---|
| 429 tenant/rate budget | Stay local | No | Policy/fairness failure must not consume fallback. |
| 500 app/tool/internal failure | Stay local | No | Retrying elsewhere hides app failure. |
| slice_oom / local bug | Stay local | No | Diagnose local allocation/config problem. |
| 503 local capacity unavailable | Overflow eligible | Yes | Capacity exhaustion can be redirected. |
| 529 overload | Overflow eligible | Yes | Explicit overload signal. |

**Overflow destination must be named in DESIGN.md:** specific provider + specific model + why it is acceptable for this workload. The overflow gate sits after the local result/decision and must preserve the original reason code in metrics.

# 12. Observability plan

| Dashboard | Panels / metrics |
|---|---|
| 1. Overview | requests, completions, goodput, end-to-end duration, sheds, overflow, errors |
| 2. Gateway + admission | accepted/rejected, orch_shed_total{reason}, tenant tokens, timeout_queue, request duration |
| 3. Router / placement | orch_pick_total{policy,worker}, unknown snapshots, prefix affinity/reused tokens, placement errors |
| 4. Queues | orch_replica_queue_depth{worker,class}, queue wait p50/p95/p99, timeout_queue |
| 5. vLLM | running, waiting, preemptions, KV utilization, prefix-cache hit/reuse, TTFT, token throughput |
| 6. KV / hop | hop_total{src,dst}, hop_tokens/bytes, hop_seconds, kv_transfer_total, kv_evict_total, misses/ghost cleanup |
| 7. GPU / DCGM | HBM used/free, GPU utilization, power, temperature if available |
| 8. Overflow | eligible/non-eligible errors, overflow count, destination, fallback latency/success |

**Per-request correlation:** attach request_id, conversation_id, agent_step, tenant_id, chosen_worker, policy, prefix_hash or prefix-id, queue time, hop decision, engine latency, final status. This lets the demo explain one request end to end.

# 13. Error taxonomy to expose

| Category | Examples | Expected metric |
|---|---|---|
| Guardrail errors | prompt_too_long, injection, malformed payload | guard_reject_total{reason} |
| Admission sheds | tenant_tokens, timeout_queue, kv_free, decode_slots | orch_shed_total{reason} |
| Placement errors | no_eligible_worker, stale_snapshot, placement_timeout | placement_error_total{reason} |
| Queue errors | queue_timeout, client_aborted | queue_error_total{reason} |
| Hop errors | missing_prefix, transfer_timeout, transfer_failed, stale_metadata | hop_error_total{reason} |
| Engine errors | OOM, 5xx, preemption spike | engine_error_total{worker,reason} |
| Overflow errors | fallback_timeout, fallback_5xx, noneligible_attempt | overflow_error_total{reason} |

# 14. Direct answers to the instructor questions

**What is the app, and which tokens are shared vs unique?**

> NYC Taxi Analytics tool-using agent. Global shared: system prompt, dataset rules, tool schemas. Conversation-shared: prior messages, tool calls, DuckDB observations. Unique: newest user/tool suffix and newly generated output.

**What dies at guardrails vs admit vs place vs queue?**

> Guardrails reject structurally unsafe/invalid work. Admission sheds valid work the cluster should not accept now. Placement fails when no eligible worker can safely take it. Gateway queue drops work whose deadline/client disappears before release to vLLM.

**Where do I prevent work that will time out?**

> Admission estimates whether queue + service time can meet the request deadline; queue enforces timeout_queue again using actual elapsed wait. Work that cannot meet deadline never enters vLLM.

**Where do I protect KV?**

> Admission reserves headroom using kv_free/tokens-in-flight; placement avoids workers with unsafe KV pressure; prefix-aware routing reuses useful KV; hop/eviction prevent unbounded external KV retention.

**Where do I prioritize interactive traffic?**

> In the gateway queue before vLLM. Interactive work gets a higher release priority than batch, subject to deadlines/fairness. vLLM then schedules only the work we have released.

**Where do I stop one tenant from owning the GPU?**

> Admission uses a tenant token/concurrency window. 429 is returned locally and is never overflowed.

**Where do I hop and what is not copied?**

> After placement if src != dst and useful KV is absent on destination. Transfer only reusable KV state/blocks and associated metadata, not model weights, request body, generated final output, or engine scheduler state.

**Where do I evict, and what becomes a ghost if I skip it?**

> Eviction occurs in local prefix/KV cache and hop-store policy when capacity/TTL requires it. If external metadata is not invalidated when a block/worker is gone, the router may believe KV exists and create a ghost cache hit, causing a failed lookup or unexpected recompute.

**Where does the engine scheduler sit vs my admit/place/queue?**

> Gateway owns admit -> place -> gateway queue. vLLM owns its own waiting/running/preemption, block table, chunked prefill, and continuous batching after a request crosses into the engine.

**What limited concurrency on this GPU for this app?**

> Hypothesis: KV capacity first for long/growing agent contexts; verify against real saturation data. Report whether KV, decode slots/compute, queueing, hop bandwidth, or warmup actually became first limiter.

**Four production alerts?**

> 1) kv_free_ratio below threshold for sustained interval; 2) interactive p99 TTFT / goodput SLO violation; 3) queue_timeout or shed rate surge by reason; 4) hop failures/stale cache metadata or vLLM preemption/OOM spike. Add absent-metric alert for expected worker metrics.

**If I scale, which pool?**

> Scale the resource that is actually hot. High uncached prefill tokens -> prefill/worker capacity. Decode slots saturated with low cache pressure -> decode capacity. Do not answer merely add a replica; show the metric that names the constrained pool.

**What changes at 10x traffic?**

> Separate interactive and batch admission budgets, strengthen prefix-aware routing, consider dedicated prefill/decode pools only if measurements justify it, externalize KV more deliberately, autoscale on queue/decode/prefill signals, and enforce stricter tenant/deadline policy.

**Which three knobs are the wrong next move?**

> 1) blindly increase max_num_seqs; 2) blindly increase max_model_len / context allowance; 3) blindly add identical replicas without identifying whether KV, prefill, decode, hop, or queueing is the limiter. These can worsen tail latency or multiply KV pressure.

**How do I measure throughput vs goodput?**

> Throughput is all completed requests/tokens per second. Goodput is only successful completions that satisfy the selected TTFT/end-to-end SLO and policy constraints. Plot both under increasing offered load to show where throughput keeps rising but useful work stops improving.

**What does a healthy memory curve look like?**

> HBM/KV rises when prefills and active sequences allocate blocks, then falls as requests finish, abort, or are evicted. Flat-at-max or monotonic growth is suspicious. Correlate memory with active sequences, prompt length, prefix reuse, and preemption.

**What should I report for a single orchestration request?**

> Total duration plus stage durations: guard, admission, placement, gateway queue, hop (if any), engine queue, TTFT, decode, tool time, and total agent-turn time. Include chosen worker, reason/policy, status code, input/output tokens, and whether SLO was met.

# 15. Final presentation flow

1. 60-90 sec: app and prompt shape - shared vs unique tokens.
2. 90 sec: capacity math - GPU/HBM, model, KV bytes/token, per-worker budget, max_len vs measured app lengths.
3. 2 min: architecture and ownership boundaries - gateway vs vLLM vs hop store.
4. 3-4 min: headline A/B results - least_loaded vs prefix_then_load; show TTFT/goodput/queue/reuse.
5. 2 min: admission overload experiment - sheds by reason and preserved interactive goodput.
6. 2 min: hop/warmup - recompute vs transfer and cold vs warm TTFT.
7. 2 min: Grafana walkthrough - overview -> gateway -> router -> queues -> vLLM -> hop -> GPU.
8. 60 sec: what actually limited the GPU, four alerts, scale decision, 10x plan and wrong knobs.

# 16. Implementation checklist

- [ ] Finalize GPU and model; capture nvidia-smi/DCGM evidence.
- [ ] Compute KV/token, KV/worker, max_len and app-length concurrency.
- [ ] Bring up Worker A/B with identical engine flags and live /metrics.
- [ ] Wire every agent model call through gateway /serve.
- [ ] Implement guard.inspect, should_shed, place.pick, per-worker queue, hop check, overflow gate.
- [ ] Emit correlated metrics with request/conversation/step/worker/policy identifiers.
- [ ] Build traffic mixes: shared-prefix, unique, multi-step, interactive+batch, noisy tenant, stale snapshot.
- [ ] Run E0-E5; keep same trace/model/flags for each A/B comparison.
- [ ] Add Dynamo comparison only after the custom baseline is measurable.
- [ ] Paste scrapes/results into DESIGN.md and notebook; export charts to plots/.
- [ ] Add four production alerts and absent-metric alerts.
- [ ] Document actual result even if hypothesis is wrong.

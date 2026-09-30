**Final Project Plan  
NYC Taxi Analytics Agent on a GPU-Aware Inference Cluster**

*Track B - Tool-using agent | Control plane + vLLM workers + KV-aware routing + hop store + observability*

**Primary thesis: prove that admission, placement, queueing, prefix/KV locality, and hop decisions improve a real multi-step agent workload under constrained GPU memory.**

## Current status (2026-09-30)

- **#140 (Slice C')**: Merged. Real conversation-history prefix contract (`render_conversation_prompt`), tool-result history (`role="tool"` messages), and pre-canned query catalogue.
- **#142 (Slice D')**: Implemented and benchmarked live on remote Lambda A100-SXM4 GPU. Automated benchmark scenario generator (`config/scenarios/`), asynchronous concurrent replayer (`ScenarioReplayer`), and zero-dependency Prometheus metrics scraper (`compute_metrics_delta`).
- **Cluster Telemetry Baseline Established**: Live runs on dual HAMi 20GB vLLM workers confirmed Worker A active processing (KV cache hit rate stepped from 0% to **29.6%**) while Worker B remained at 0% (confirming the #121 single-worker passthrough baseline as a clean control group for upcoming routing experiments). Prefill vs decode analysis proved the workload is $> 98\%$ decode-bound with sub-60 ms TTFT.
- **Next Milestone**: **#122** (GPU-aware guard/admission/placement/queue control plane) — implements `prefix_then_load` vs `least_loaded` routing and admission deadline shedding. See `docs/inference-testing-guide.md` for test procedures.

## Next-session priorities (in order)

1. **Merge #142 (Slice D')** — All 4 benchmark scenarios executed live, PR description updated with telemetry, all Copilot review comments resolved, CI passing.
2. **#122** (GPU-aware guard/admission/placement/queue control plane) — the central milestone implementing the gateway pipeline: `app -> guard.inspect() -> should_shed() -> place.pick() -> per-worker gateway queue -> hop check -> vLLM`.
3. **#133** (KV hop store integration / Mooncake/LMCache) — evaluating recompute vs network hop.
4. **#123** (Controlled experiments E0–E5 & Evidence Matrix) — final experimental proof with Jupyter notebook artifacts.
5. **#139** (Configurable agent strategy / CrewAI) — comparative evaluation of agent orchestration strategies.

# 1. Project thesis and success criteria

> **The Unified Thesis**: Keep the taxi agent, run it on a real two-worker vLLM setup, make prefix/KV behavior visible, own guard/admit/place/queue decisions, compare least-loaded vs KV-aware routing and recompute vs hop, then prove every choice with goodput, TTFT, memory, queue, and Grafana evidence.

### Core Strategic Principles:
1. **Stay with Track B — Tool-Using Agent**: The NYC Taxi Analytics app is already a natural multi-step workload (model -> tool -> observation -> model). The application itself does not need excessive product polish; the focus is the serving behavior and reasoning about infrastructure choices.
2. **Reasoning About Serving Decisions at the Center**: Rather than merely demonstrating that requests complete, the project explains *why* one request is handled differently from another—when contexts grow, when shared prefixes become critical, and why a specific worker was chosen.
3. **Deliberate Prefix Architecture**: Distinct 3-tier prefix hierarchy: global shared prefix (system prompt + tool schemas), conversation-shared prefix (prior turns/observations), and unique newest suffix.
4. **Gateway Control Plane Pipeline**: Mirror the canonical pipeline:
   `app -> guard.inspect() -> should_shed() -> place.pick() -> per-worker gateway queue -> hop check -> vLLM waiting/running -> response -> tool -> next agent step`.
5. **KV-Aware Routing as the Primary Experiment**: Compare `least_loaded` against `prefix_then_load` under identical traffic traces.
6. **Mooncake / LMCache for the KV Hop Problem**: Treat Mooncake/LMCache as the hop layer when the chosen worker lacks KV cache; measure the recompute vs. hop crossover across prefix lengths.
7. **NVIDIA Dynamo as Comparison**: Implement and measure the simple custom policy first, then compare against Dynamo KV-aware routing on the same workload.
8. **Two Workers on 50/50 HAMi vGPU**: Start with two virtual GPU slices on a single physical A100 to make same-device contention and memory behavior visible before scaling.
9. **Capacity Math Before Tuning**: Calculate exact KV bytes/token, decode slots, KV block budgets, hop bandwidth, and warmup time.
10. **Goodput Over Throughput**: Interactive goodput ($TTFT \le 100\text{ ms}$, $E2E \le 3.5\text{ s}$) represents useful work; raw throughput alone can hide interactive collapse.
11. **Three-Plane Triage Model**: Decompose failures across the Data Plane (tool/retrieval), Control Plane (admission/routing), and GPU Plane (vLLM scheduler/KV/prefill/decode). See `docs/inference-testing-guide.md`.
- Proof standard: every design choice must have a file, scrape, notebook cell, or Grafana panel that shows what happened.

# 2. Target architecture and execution boundary

```text
Local Taxi Analytics App
      |
      | OpenAI-compatible request through SSH tunnel initially
      v
Lambda Gateway / Control Plane :8080
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

The existing FastAPI app, MCP/DuckDB service, web application, product observability, and durable state stay local. The gateway/control plane, vLLM workers, cluster observability, and controlled traffic runner execute on Lambda. Only `infra/inference/` is transferred. Remote run artifacts are pulled back into local `metrics/inference/`. See [ADR 0010](decisions/0010-transferable-lambda-inference-lab.md) for the ownership, provider, SSH, and capacity-measurement decisions.

# 3. Repository structure

```text
# Local product and canonical evidence; never copied wholesale to Lambda
services/app/                    # FastAPI agent and LLMClient providers
services/mcp/                    # FastMCP and DuckDB
web/
observability/                   # existing product observability
experiments/                     # local analysis/notebooks, when added
metrics/inference/               # pulled scrapes, manifests, derived data

# The only remote-transferable subtree
infra/inference/
  README.md
  gateway/                       # thin serve path, then control-plane policies
  k8s/
    hami/
    workers/
    services/
    lmcache/                     # only when real integration is attempted
    mooncake/                    # only when real integration is attempted
  observability/
    prometheus/
    grafana/
    dcgm/
  experiments/                  # runners execute near the workers
  scripts/                       # sync, tunnel, deploy, smoke, pull, teardown
```

This is the target layout, not permission to pre-build future issues. #120 creates the remote cluster/capacity slice; #121 adds the serve path and provider adapter; minimal #115 adds the two workload shapes; #122 adds real gateway policies; #123 owns the final controlled experiments.

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
| Conversation prefix | Within one conversation | Prior user messages, tool calls, DuckDB observations | Becomes increasingly valuable on later turns. |
| Unique suffix | Per inference step | Newest question, newest tool result, latest reasoning/answer suffix | Must be prefetched/decoded anew. |

**Note (2026-09-29):** the conversation-prefix region above described the intended shape all
along, but was not actually sent to the model until #115 slice C' (real conversation history via
`render_conversation_prompt`, tool results persisted as `role="tool"` messages). "Later ReAct
steps" now means "later turns in a scripted/catalogue-driven conversation," not a model-driven
multi-step investigation loop — see Current status above.

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
| E5 Hop | Recompute on destination vs LMCache/Mooncake hop | Cross-worker continuation at 1K/2K/4K/7K prefixes within the 8,192-token worker context; 16K/32K only after workers are redeployed with a larger `max_model_len`, recorded in the run manifest | Hop ms/bytes, TTFT, tokens recomputed, crossover point | When is moving KV cheaper than rebuilding it? |
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

**Overflow destination must be named in the active decision record and run manifest:** specific provider + specific model + why it is acceptable for this workload. The overflow gate sits after the local result/decision and must preserve the original reason code in metrics.

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

# 15. Evidence matrix and project-completion acceptance gate

The answers in Section 14 describe the intended architecture. The project is complete only when each question below points to concrete measured evidence. A design statement, response header, empty dashboard, or uncorrelated cumulative counter does not satisfy this gate.

| Question | Required final evidence | Owner |
|---|---|---|
| What is the app, and which tokens are shared versus unique? | Exact-token counts for global shared, conversation-shared, and unique regions; a versioned prefix identity; and a growing multi-step taxi-agent trace. Record model, tokenizer, chat-template, and prefix-contract revisions. | #115 |
| What dies at guardrails versus admit versus place versus queue? | Bounded counters and stable reason taxonomy for every stage plus representative correlated requests showing where execution stopped. | #122 |
| Where is work prevented from timing out? | One admission rejection where estimated queue plus service time cannot meet the deadline and one actual `timeout_queue` expiry in the gateway queue. Prove that neither request entered vLLM. | #122 |
| Where is KV protected? | The admission/placement snapshot containing KV headroom, tokens in flight, running/waiting work, queue depth, estimated uncached/reusable tokens, and snapshot age; the resulting decision; and the corresponding worker KV time series. | #122, #123 |
| Where is interactive traffic prioritized? | Queue-wait and latency distributions by bounded workload class, interactive deadline-goodput, and a batch-starvation check. Derive `class_p99_spread = batch_queue_wait_p99 - interactive_queue_wait_p99`; a favorable spread alone is not success if batch work starves. | #122, #123 |
| Where is one tenant prevented from owning the GPU? | A noisy synthetic-tenant run showing token/concurrency enforcement, local `429` behavior, admitted/completed/shed work, fairness calculation, and protected interactive goodput. | #122, #123 E4 |
| Where does a KV hop occur, and what is not copied? | A real compatible-block transfer with source/store and destination provenance, tokens/bytes, duration, result, and destination consumption. State explicitly that model weights, request bodies, generated output, and engine scheduler state are not transferred. | #133, #123 E5 |
| Where does eviction occur, and what becomes a ghost if invalidation is skipped? | Eviction and metadata-invalidation events plus a stale-metadata/ghost-cache prevention or failure test. | #133 |
| Where does the engine scheduler sit relative to admit, place, and the gateway queue? | A correlated timeline for guard, admission, placement, gateway-queue entry/release, and worker dispatch beside vLLM engine queue, waiting/running, prefill, decode, and preemption evidence. | #122, #123 |
| What limited concurrency on this GPU for the real app? | A taxi-agent saturation run at measured application context lengths with a first-limiter classification and the corresponding SLO/goodput point. The earlier synthetic #120 capacity sweep is preliminary evidence, not the final application answer. | #123 E0 |
| Which four production alerts would be set? | Four concrete PromQL rules and threshold rationales grounded in observed metrics: sustained KV pressure; interactive latency/goodput SLO failure; queue timeout or shed-rate surge; and worker/hop/engine integrity failure. | #123 |
| If the system scales, should capacity be added to prefill or decode? | Same-window uncached prompt rate, cache hits, prefill time, and TTFT compared with running-slot pressure, generation rate, decode time, ITL, and goodput. Name the hot pool from the measurements. | #123 |
| What changes at 10x traffic, and which three knobs are the wrong next move? | A measured or trace-driven 10x analysis grounded in the observed bottleneck. Explicitly assess blindly increasing `max_num_seqs`, increasing context allowance, and adding identical replicas without identifying the constrained pool. | #123 |
| How is throughput measured versus goodput? | Raw requests/s and tokens/s plotted beside good requests/s and good tokens/s under increasing offered load, from the same run, with the frozen TTFT/end-to-end SLO and the goodput formula from Section 9. Must show where throughput keeps rising while goodput flattens or falls (or report that it does not). | #123 |
| What does a healthy memory curve look like? | A same-window time series of DCGM HBM used/free, vLLM KV cache usage, running/waiting sequences, request rate, preemptions, and hop/eviction events (Section 10), showing KV/HBM rising with prefill/active sequences and releasing on completion/abort/eviction. Flag any flat-at-max or monotonic growth as a finding. | #123 (hop/eviction series from #133) |
| What is reported for a single orchestration request? | One correlated end-to-end request record with stage durations for guard, admission, placement, gateway queue, hop (if any), engine queue, TTFT, decode, tool time, and total agent-turn time, plus chosen worker, policy/reason, status code, input/output tokens, and SLO outcome. Each field is marked per-request measured or explicitly unavailable, never inferred from aggregate counters. | #122, #123 |

## Final-run evidence rules

- Run the controlled proof only after #115 and #122 are stable. E0-E4 and the E5 recompute control may proceed before #133; the E5 real-hop treatment requires #133.
- Use the same workload trace, model/revision, tokenizer/template, engine flags, worker allocation, SLOs, and topology for every control/treatment comparison.
- State whether every reported value is per-request, isolated Prometheus-window, or cumulative-scrape evidence.
- Never assign an aggregate cache, recomputation, queue, or timing delta to one request without a valid correlation mechanism.
- Keep request, conversation, tenant, and prefix identifiers in correlated logs or run artifacts rather than unbounded Prometheus labels.
- Record whether Worker A and Worker B are separate replicas on one physical GPU or separate physical GPUs; do not describe worker movement as GPU-to-GPU movement unless the topology supports that statement.
- A worker change proves placement only. An independently warmed destination prefix proves destination-local reuse. Claim a KV hop only when real compatible KV blocks become available to the destination with transfer provenance and consumption evidence.
- The disposable-machine workflow remains unchanged during implementation. Current checkpoints may use `make inference-pull-evidence`; full controlled evidence is collected when the final experiments are ready.

## Completion checklist

- [ ] Every evidence-matrix row links to a concrete committed file, pulled final-run artifact, Prometheus scrape/query, Grafana panel, or notebook cell.
- [ ] No row is supported only by a planned metric, design statement, response header, or empty dashboard.
- [ ] Every dashboard panel used in the final explanation has a live underlying metric.
- [ ] Per-request conclusions use correlated evidence; isolated-window and cumulative values are labeled honestly.
- [ ] Control and treatment manifests prove matching workload, model, engine, SLO, and topology inputs.
- [ ] The final proof includes both Worker A and Worker B and discloses same-physical-GPU contention when applicable.
- [ ] Scaling, alerting, and 10x recommendations cite observed measurements rather than generic guidance.
- [ ] Negative, neutral, or inconclusive results are reported without being converted into unsupported success claims.

# 16. Final presentation flow

1. 60-90 sec: app and prompt shape - shared vs unique tokens.
2. 90 sec: capacity math - GPU/HBM, model, KV bytes/token, per-worker budget, max_len vs measured app lengths.
3. 2 min: architecture and ownership boundaries - gateway vs vLLM vs hop store.
4. 3-4 min: headline A/B results - least_loaded vs prefix_then_load; show TTFT/goodput/queue/reuse.
5. 2 min: admission overload experiment - sheds by reason and preserved interactive goodput.
6. 2 min: hop/warmup - recompute vs transfer and cold vs warm TTFT.
7. 2 min: Grafana walkthrough - overview -> gateway -> router -> queues -> vLLM -> hop -> GPU.
8. 60 sec: what actually limited the GPU, four alerts, scale decision, 10x plan and wrong knobs.

# 17. Implementation checklist

- [ ] Finalize GPU and model; capture nvidia-smi/DCGM evidence.
- [ ] Compute KV/token, KV/worker, max_len and app-length concurrency.
- [ ] Bring up Worker A/B with identical engine flags and live /metrics.
- [ ] Wire every agent model call through gateway /serve.
- [ ] Implement guard.inspect, should_shed, place.pick, per-worker queue, hop check, overflow gate.
- [ ] Emit correlated metrics with request/conversation/step/worker/policy identifiers.
- [ ] Build traffic mixes: shared-prefix, unique, multi-step, interactive+batch, noisy tenant, stale snapshot.
- [ ] Run E0-E5; keep same trace/model/flags for each A/B comparison.
- [ ] Add Dynamo comparison only after the custom baseline is measurable.
- [ ] Pull each run manifest and scrape set into `metrics/inference/<run-id>/`; keep derived notebooks and charts under local `experiments/` when added.
- [ ] Add four production alerts and absent-metric alerts.
- [ ] Document actual result even if hypothesis is wrong.

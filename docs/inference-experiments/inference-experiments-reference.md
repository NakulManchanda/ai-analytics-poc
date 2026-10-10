# Experiments E0-E5 reference (#123)

Quick "what is each one" sheet. Sources: [inference-testing-guide.md](inference-testing-guide.md) s.3-4, [inference-run-playbook.md](inference-run-playbook.md), and the
session runbook [inference-session-runbook.md](inference-session-runbook.md) (run order and checkpoints). Planned run order: E1, E2, E3, E4, then E0 + memory proof. E5 has two
legs: the no-transfer controls, and the real KV hop (#133, merged in PR #154) which needs a separate GPU session on the KV bundle.

**Naming caveat:** the testing guide s.3 and the runbook/playbook describe E0 and E2 differently (below). The runbook and
playbook are what you actually run, so follow those.

## At a glance

| # | Name | Question it answers | Control vs treatment | Needs controls on gateway? |
|---|---|---|---|---|
| E0 | Capacity / first limiter | What limits this GPU for this app: KV, decode slots or queue? | Offered-load sweep (concurrency 1..16) | no |
| E1 | Cold vs declared-warm worker | How much slower is the first request after a restart? | cold (right after restart) vs warm (minutes later) | no |
| E2 | Prefix reuse | Does a reused prefix cache cut TTFT? | first pass (cold cache) vs second pass (reused), same trace | no |
| E3 | Routing | `least_loaded` vs `prefix_then_load`: does prefix affinity help? | same trace, two placement policies | yes (`ALLOW_EXPERIMENT_CONTROLS=1`) |
| E4 | Admission on vs off | Does shedding under overload protect interactive goodput? | same overload trace, admission on vs off | yes, plus `TENANT_ALLOWLIST` |
| E5 | Recompute vs hop | At what prefix size would transferring KV beat recomputing? | local reuse vs recompute control vs destination hit (no transfer), then the real hop via `inference-kv-smoke` | yes (`ALLOW_FORCED_PLACEMENT=1`) |

## E0: capacity and first limiter

- **Runbook version:** `make inference-capacity` (synthetic) plus an application-shaped sweep of `e3_routing_mixed` at concurrency
  1, 2, 4, 8, 12, 16 through the gateway, then `inference-pull-range` for the memory series.
- **Look for:** the knee in `sweep.csv`, where goodput flattens or falls while throughput still rises. The memory range tells you
  whether KV, decode slots or queue hit first. Record the answer to "what limited this GPU for this app?".
- **Guide version ("paper capacity math"):** compute KV bytes per token = 2 x layers x kv_heads x head_dim x 2 bytes, subtract
  weights and workspace from the slice, and predict max concurrent sequences. It is the prediction the sweep is checked against.
  (The guide's example model, Qwen 2.5 Coder 7B, is not the deployed Qwen3-0.6B.)
- **Also gives the memory proof:** KV, HBM, running/waiting and preemptions over the busiest window.

## E1: cold vs declared-warm

- **Steps:** restart workers (`make inference-restart`, `restart-test.sh` for worker A), run `warmup.py` immediately (cold),
  again after several minutes (warm), then `make inference-pull-evidence`.
- **Look for:** first-request TTFT in `cold/warmup_summary.json` visibly higher than warm p50/p95. If not, the restart did not
  go cold. The guide also lists weight-loading and CUDA-graph capture time as things to observe.

## E2: prefix reuse (cold vs reused)

- **Runbook version:** restart both workers, run the declared warm-up so only the prefix cache is cold, then run the same
  replay twice with no restart in between (`e2-cold`, `e2-reused`).
- **Look for:** `comparable: true` for the pair, and a TTFT drop on the second pass. Wording matters: it is "first pass vs fully
  warm", not "no cache vs cache". Prefix-cache deltas are window-level and from one worker.
- **Guide version ("two-worker baseline & KV pressure"):** identical bursts, Worker A active and Worker B idle as the control.

## E3: routing, `least_loaded` vs `prefix_then_load`

- **Steps:** restart both workers before EACH run, then `make replay-e3-least-loaded` and `make replay-e3-prefix-then-load`
  on the headline trace `e3_routing_mixed` (10 multi-turn taxi conversations sharing the app's real system prefix).
- **Expected finding, not a bug:** with the real ~270-token prefix, `prefix_then_load` sticks on turn 1 and spills to load-based
  placement from turn 2 (`prefix_overlap_low`), because the overlap drops below the 0.8 gate as the conversation grows. The
  guide's older "sub-30ms TTFT from turn 2" hypothesis is the optimistic case, and the shipped trace deliberately does not tune
  for it.
- **Optional:** the synthetic large-prefix variant (`replay-e3-large-prefix-*`, prefix padded to ~6k tokens) shows affinity when
  the prefix dominates. It is a separate, labelled experiment.
- **Fails fast (exit 2)** if the gateway does not echo the policy header: controls are off.

## E4: admission on vs off

- **Steps:** the commands live in the playbook's E4 section (and are not repeated here). In short: `make inference-refresh` brings a
  running cluster to the manifests (tenant cap `TENANT_MAX_CONCURRENCY=10` per tenant, controls on); wait for `worker_warm == 1`
  and `worker_ramp_cap == 0`; then `make replay-e4-admission-off` and `make replay-e4-admission-on`.
- **Why a cap of 10:** the cap is per tenant and `MAX_DECODE_SLOTS=8` is per worker (16 slots). At the old default of 4, three
  tenants hold at most 12 in flight, so `decode_slots` could never fire and `tenant_interactive` would be 429'd itself.
- **Look for:** off, interactive p99 and goodput degrade (queueing, `timeout_queue`); on, sheds appear by reason (`decode_slots`,
  `tenant_concurrency` for `tenant_noisy`, and possibly `kv_pressure` / `deadline_unachievable`) and interactive goodput holds.
  Fairness (Jain) and batch starvation are in `e4_compare`. Watch the alerts fire and record which.
- **Expected from the code, not yet observed:** `deadline_unachievable` (504) may not fire, because its estimate uses vLLM
  `waiting` only while the gateway holds the queue (`WORKER_MAX_INFLIGHT` equals `--max-num-seqs`). Report what actually fires.
- **Proof from the guide:** shed requests never enter vLLM's queue, so `vllm:num_requests_waiting` does not rise for them.
- **Note:** `x-admission-mode: off` skips only capacity/deadline shedding; tenant quota still runs. Client latency through the
  SSH tunnel is not valid evidence (goodput stays 0); use sheds, queue, fairness and vLLM metrics.

## E5: recompute vs hop

- **Control legs (no transfer):** a control with no KV transfer, at sizes 1k, 2k, 4k, 7k (chars/4 estimate; exact tokens
  are in each manifest):
  - `e5_local_reuse_<size>`: A to A, local reuse possible.
  - `e5_recompute_control_<size>`: A to B with no transfer, so B must prefill it itself. This is the control.
  - `e5_destination_hit_<size>`: B warmed separately, then continued from A to B. B hits its own cache; not a hop.
  - `e5_local_eviction_4k`: inconclusive unless vLLM evidence shows eviction happened.
- **Run:** `make replay-e5 E5_SCENARIO=<name> ...`. Restart both workers before each scenario.
- **Real hop (#133, merged):** `make inference-kv-smoke PREFIX_SIZES="1k 2k 4k 7k"` on the LMCache/Mooncake bundle, run in the same
  session as the controls (see the playbook E5 section). It writes `crossover.json` with recompute vs transfer TTFT/E2E per size.
  The live GPU run has not happened yet, so there is no hop evidence until it retains a passing `validation.json`. Never claim a hop from a worker change or a
  lower latency. A 7k case can be rejected (prompt + `max_tokens` over 8192); drop to a smaller size.

## Memory proof

Not a numbered experiment: `make inference-pull-range` over the busiest window (the E0 sweep or the E4 pair). `memory_proof`
flags flat-at-max, growth without frees, preemptions and idle windows. Flat HBM is normal because vLLM preallocates; the KV
series is the informative one.

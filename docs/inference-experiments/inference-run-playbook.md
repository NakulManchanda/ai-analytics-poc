# Inference evidence run playbook (#123 E0-E5 + memory proof)

Ordered commands for a cluster session that produce the artifacts the local analysis toolkit
(`experiments/analysis`) and notebook (`experiments/123_evidence.ipynb`) consume. Background and
experiment meaning: [inference-testing-guide.md](inference-testing-guide.md) section 4 and
[inference-project-plan.md](../inference-project-plan.md) sections 8-9 and "Final-run evidence rules".
All commands run from the repository root (or the active worktree) unless noted.

## Start here: new instance to cluster-ready

Needs the instance IP in `infra/inference/.env` (`LAMBDA_SSH_HOST`) and as the `lambda` host in `~/.ssh/config`. On a running
cluster use `make inference-refresh` instead of step 1. Stop on any failure.

```bash
make inference-fresh-up          # 1. sync, bootstrap, Secrets, deploy, controls ON, verify (long; use tmux)
make inference-tunnel            # 2. own terminal, leave running
curl -s http://127.0.0.1:18080/health; curl -s http://127.0.0.1:18001/health; curl -s http://127.0.0.1:18002/health   # 3.
curl -s http://127.0.0.1:18080/metrics | grep -E '^worker_(warm|ramp_cap)\{'   # wait for warm 1 / ramp_cap 0 on both
make inference-verify-workers    # 4. workers identical
ssh lambda 'sudo k3s kubectl -n inference-lab get secret' | grep -E "NAME|overflow|hf-token"
make inference-smoke             # 5. engine, then serve path
make inference-serve-smoke
ssh lambda 'sudo k3s kubectl -n inference-lab set env deploy/inference-gateway --list' | grep -E "ALLOW_|TENANT|MAX_DECODE|OVERFLOW"   # 6. controls 1, cap 10, overflow 0
```
Section 1 is the full pre-flight before each experiment.

## 0. Conventions

- **Run id:** `RUN_ID=d123-<YYYYMMDD>-<experiment>` (for example `d123-20261001-e3`). The manifest env files in `infra/inference/experiments/manifest/` add a rerun suffix, `d123-<YYYYMMDD>-<experiment>-<HHMM>` (local time), so reruns do not overwrite; the `export RUN_ID=...` lines below are the plain form. Everything for one
  experiment lands under `metrics/inference/$RUN_ID/` (gitignored):
  - `metrics/inference/$RUN_ID/<scenario>_<strategy>_<timestamp>/` one directory per replayer run:
    `requests.jsonl` (per-request), `summary.json` (per-level summary plus `prometheus_window`),
    `sweep.json` / `sweep.csv`, `manifest.json`;
  - `metrics/inference/$RUN_ID/prometheus_range/<query>.json` from `make inference-pull-range`
    (WINDOW-level time series);
  - `metrics/inference/$RUN_ID/` cluster snapshots from `make inference-pull-evidence RUN_ID=$RUN_ID`;
  - `warmup_summary.json` / `capacity_summary.json` when the #120 runners get `--output-dir`.
- **Replayer output dir:** pass `REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"` (the default is
  `metrics/evidence`). `--label <name>` overrides the policy label in the manifest (last flag wins).
- **Manifest inputs** (exported before `make replay-*`; otherwise recorded as `unknown` and the comparison
  warns that a match is not provable): `MODEL_REVISION`, `TOKENIZER_REVISION`, `CHAT_TEMPLATE_REVISION`,
  `ENGINE_FLAGS` (the workers' real vLLM flags), `VLLM_VERSION`, `KV_BLOCK_SIZE`, `INFERENCE_TOPOLOGY` (state whether Worker A/B are two HAMi
  slices on one physical GPU or separate GPUs), optional `INTERACTIVE_TTFT_SLO_MS`, `E2E_SLO_MS`. The
  manifest also records scenario sha256, prefix size (set `TOKENIZE_URL` or rely on
  `<target-url>/tokenize`), controls requested/verified, and SLOs. Use the SAME values for every
  control/treatment pair. Manifests written before PR #151 have arm-dependent scenario hashes and no
  `execution` block; regenerate those runs rather than comparing them.
- **Isolated windows:** `--metrics-url` scrapes ONE worker's `/metrics` before/after a run, so its delta is
  a window aggregate for that worker only. Run nothing else against the cluster during a measured run,
  do not overlap runs, and never attribute window deltas to a request. For the two-worker picture use the
  Prometheus range export over the run window.
- **Ports** (tunnel): gateway `18080`, worker A `18001`, worker B `18002`, Prometheus `19090`, Grafana `13000`.
- **Where the driver runs:** the laptop, through the SSH tunnel, as for E0-E3 (the on-host variant is not used for now).
  Consequence: client TTFT/E2E include tunnel latency (about 2 s TTFT for a 244-token prompt in E0), so SLO goodput
  stays 0 and client latency is NOT a valid SLO or calibration source. The valid E4 evidence is gateway-side:
  sheds by reason, `timeout_queue`, per-tenant fairness, queue depth, and vLLM server-side metrics from Prometheus.
- **Session order (E4 and E5 only):** pre-flight (section 1) -> calibration (section 1a) -> E4 on the plain image ->
  apply the KV bundle -> E5 control legs -> E5 real hop -> section 5 (restore the normal manifests).
  E4 must run BEFORE the KV bundle is applied; do not compare across the two images.

## 1. Pre-flight checklist (once per session)

1. `make inference-tunnel` in a dedicated terminal (or `make inference-connect` to sync first).
2. Workers healthy and warm: `curl -s http://127.0.0.1:18001/health`, `:18002/health`, `:18080/health`;
   gateway `worker_warm` and `worker_health` are 1 for both workers (Grafana overview, or
   `curl -s http://127.0.0.1:19090/api/v1/query --data-urlencode 'query=worker_warm'`).
   Then smoke the engine before debugging the serve path, in this order, and stop on any failure:
   `make inference-smoke` (probes each worker directly), then `make inference-serve-smoke` (gateway
   `/serve`: stage headers `x-guard-decision`, `x-admit-decision`, `x-place-decision`,
   `x-queue-decision` on an accepted request, one tool-calling agent step, and two guard rejects).
3. Dashboards and alerts: Grafana at `http://127.0.0.1:13000` shows Overview, Gateway+admission, Router,
   Queues, Overflow and Memory Proof with live data; Prometheus `/alerts` lists the four alerts.
   (`make inference-dashboards` regenerates dashboards locally if they drifted.)
4. Gateway env for controlled runs (test-only; turn off again afterwards). `set env` triggers a rollout:
   ```bash
   source infra/inference/.env   # LAMBDA_SSH_* values
   ssh -i "$LAMBDA_SSH_KEY_PATH" "$LAMBDA_SSH_USER@$LAMBDA_SSH_HOST" \
     'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; kubectl -n inference-lab set env deploy/inference-gateway \
        ALLOW_EXPERIMENT_CONTROLS=1 TENANT_ALLOWLIST=tenant_interactive,tenant_noisy,tenant_batch \
      && kubectl -n inference-lab rollout status deploy/inference-gateway --timeout=2m'
   ```
   Add `ALLOW_FORCED_PLACEMENT=1` only for forced-worker runs (E5, not needed for E0-E4). Note
   `make inference-deploy` resets these to the manifest values. E4 also needs admission thresholds tuned so the
   trace really overloads two workers (`MAX_DECODE_SLOTS`, `KV_FREE_MIN`, `PREFILL_TOKENS_PER_S`,
   `QUEUE_WAIT_PER_WAITING_S`; see the testing guide).
   The gateway manifest now sets the admission, queue, guard and warm-gate limits from the engine flags
   (see `docs/work-history/0086-inference-gateway-hardening-step1.md`), and a worker takes traffic only after it
   passes the warm gate, so wait for `worker_warm == 1` before measuring.
5. Schema/prefix check (manual): start the local MCP (`make mcp-dev`), read its dataset-schema resource and
   confirm the column list equals `CANONICAL_TAXI_SCHEMA["columns"]` in
   `services/app/app/benchmarks/canonical_prefix.py`. If it differs, update that file and run
   `uv run --project services/app pytest services/app/tests/test_scenarios.py` before any E3 run.
6. Tokenizer count: confirm `curl -s -X POST http://127.0.0.1:18080/tokenize -H 'content-type: application/json'
   -d '{"model":"Qwen/Qwen3-0.6B","prompt":"hello"}'` returns a `count`. The replayer writes the exact prefix
   token count into every manifest (`system_prefix.exact_tokens`; `null` plus a reason if unreachable).
7. Export the manifest env vars from section 0 and check the local toolchain:
   `uv run --project services/app pytest tests/experiments -q`.

## 1a. Admission calibration (before E4; replaces the placeholder `PREFILL_TOKENS_PER_S` / `QUEUE_WAIT_PER_WAITING_S`)

`k8s/gateway/gateway.yaml` ships `PREFILL_TOKENS_PER_S=4000` and `QUEUE_WAIT_PER_WAITING_S=0.25` as placeholders.
`deadline_unachievable` is `queue_wait + est_tokens / PREFILL_TOKENS_PER_S` versus the request deadline, so E4 needs
measured values. Use SERVER-SIDE vLLM metrics (Prometheus on `19090`), never client timings through the tunnel.

1. Load: do NOT run a separate sweep. The E0-style sweep sends no tenant, so it lands in the `other` bucket (max concurrency 4)
   and returns 429 above 4 unless the quota is raised, and any gateway restart re-runs the warm gate and ramp (wait for
   `worker_ramp_cap` 0). Instead read the values over the E4 window itself (E4 section, step "record the observed prefill rate").
   Note: the deadline estimate uses vLLM `waiting` only, and the gateway holds the queue (`WORKER_MAX_INFLIGHT` equals
   `--max-num-seqs`), so these two values may barely matter and `deadline_unachievable` may not fire; report what fires.
2. Read the window averages (verify the metric names with `curl -s http://127.0.0.1:18001/metrics | grep -E 'prefill_time|queue_time|prompt_tokens'`):
   ```bash
   P=http://127.0.0.1:19090/api/v1/query; W=<window-seconds>
   # prefill rate (tokens/s) = prompt tokens / prefill seconds, per worker (take the lower of the two)
   curl -s $P --data-urlencode "query=sum(increase(vllm:request_prompt_tokens_sum[${W}s])) by (pod) / sum(increase(vllm:request_prefill_time_seconds_sum[${W}s])) by (pod)"
   # engine queue wait while saturated (concurrency 16 vs 8 slots): mean seconds in vLLM's waiting queue
   curl -s $P --data-urlencode "query=sum(increase(vllm:request_queue_time_seconds_sum[${W}s])) by (pod) / sum(increase(vllm:request_queue_time_seconds_count[${W}s])) by (pod)"
   curl -s $P --data-urlencode "query=avg_over_time(vllm:num_requests_waiting[${W}s])"
   ```
   `PREFILL_TOKENS_PER_S` = the prefill-rate figure. `QUEUE_WAIT_PER_WAITING_S` = mean queue time / mean
   `num_requests_waiting` in the saturated part of the window. Use a conservative (lower rate, higher wait) value.
3. Apply and record (the gateway restarts; wait for `/health` 200 and `worker_warm == 1`):
   ```bash
   ssh lambda 'sudo k3s kubectl -n inference-lab set env deploy/inference-gateway PREFILL_TOKENS_PER_S=<n> QUEUE_WAIT_PER_WAITING_S=<s> MAX_DECODE_SLOTS=8'
   ```
   Write the chosen values, the window, and the three query results into `metrics/inference/$RUN_ID/calibration.md`.
   Also put the same values in `gateway.yaml` afterwards (a PR) so the next session starts from them.
   `make inference-deploy` resets these to the manifest, so re-apply after any redeploy.

**Refreshing a running cluster.** After changing manifests, gateway code or Secrets locally, `make inference-refresh` (sync, Secrets,
deploy, controls ON, verify workers) brings the live cluster to the current files without a bootstrap. `make inference-deploy`
resets gateway env to the manifest (controls OFF), which is why the target turns them back on.

## 2. Cold-start recipe (used by E1 and E2)

A cold run needs empty prefix caches and freshly started engines:

```bash
make inference-restart                                   # rollout-restarts inference-worker-b, waits for recovery
bash infra/inference/scripts/restart-test.sh inference-worker-a metrics/inference/$RUN_ID   # same for worker A
```
Restart the gateway only if its env/code changed (`make inference-gateway-restart`); the gateway holds no
KV state, but its prefix-affinity beliefs and snapshot cache reset on restart, so restart it too when a run
must start from a clean placement state. Wait for `worker_warm == 1` before measuring.

## 3. Experiments

### E0 Capacity and saturation

```bash
# Step 0: the sweep sends no tenant, so it lands in the `other` bucket (max concurrency 4 by default). Raise it for E0 only,
# or the sweep above 4 concurrent returns 429 tenant_concurrency. Gateway restarts (~30 s); check /health on 18080 afterwards.
# Before E4 set the E4 cap instead (`TENANT_MAX_CONCURRENCY=10`, see E4); `TENANT_MAX_CONCURRENCY-` (trailing dash) unsets it back to the default 4.
ssh lambda 'sudo k3s kubectl -n inference-lab set env deploy/inference-gateway TENANT_MAX_CONCURRENCY=64'
source infra/inference/experiments/manifest/e0-capacity.env   # once per experiment; sets RUN_ID=d123-<date>-e0-<HHMM>
make inference-capacity RUN_ID=$RUN_ID            # preliminary synthetic capacity: capacity_summary.json
# Application-shaped offered-load sweep through the gateway (taxi-shaped multi-turn trace, no policy override):
uv run --project services/app python services/app/scripts/run_scenario.py --scenario e3_routing_mixed \
  --endpoint-type gateway_chat --target-url http://127.0.0.1:18080 --output-dir metrics/inference/$RUN_ID \
  --sweep-concurrency 1,2,4,8,12,16 --label e0-sweep
make inference-pull-range RUN_ID=$RUN_ID          # after the sweep; default window is the last 30 minutes
make evidence-analyze RUN="metrics/inference/$RUN_ID/<run_dir>" RANGE=metrics/inference/$RUN_ID/prometheus_range
```
(The direct command is used because E0 has no policy override.) Produces `sweep.csv`: offered load vs throughput vs goodput and the
knee; the memory range gives the first-limiter evidence (KV, slots, queue). The capacity runner is
synthetic and preliminary; the sweep is the application answer.

### E1 Cold vs declared-warm worker

```bash
export RUN_ID=d123-$(date +%Y%m%d)-e1
# cold: immediately after the restart in section 2
python3 infra/inference/experiments/warmup.py --output-dir metrics/inference/$RUN_ID/cold
# declared warm: again after several minutes of normal operation
python3 infra/inference/experiments/warmup.py --output-dir metrics/inference/$RUN_ID/warm
make inference-pull-evidence RUN_ID=$RUN_ID       # pod start-to-ready timing, worker logs, hardware
```
Produces `cold/warmup_summary.json`, `warm/warmup_summary.json` (first request = unwarmed TTFT, then warm
p50/p95). Notebook keys `E1_WARMUP_COLD` / `E1_WARMUP_WARM`.

### E2 Prefix reuse (cold vs reused)

Both runs use the same existing scenario and policy so only cache state differs. These must be IDENTICAL in
the two manifests or `e2_prefix_reuse` reports `comparable: false` (numbers only under `not_comparable_numbers`):
scenario hash, `execution.policy_override`, `execution.admission_mode`, `router_label`, `gateway_stream`,
SLOs, topology, model/tokenizer/chat-template revisions, engine flags, vLLM version, KV block size, `max_tokens`,
prefix size and offered-concurrency levels. Missing or `unknown` values also make the pair unproven
(`proven: false`), so export `VLLM_VERSION`/`KV_BLOCK_SIZE` and the revision variables per section 0.
The same gate applies to E3 and E4: only `policy_override` (E3), respectively `admission_mode` (E4), may differ,
and that control must be concrete and DIFFERENT in the two manifests; the same value twice, or a missing/`unknown`
one, makes the pair `comparable: false` (`manifest_check.treatment_problems`). E2 varies no control.
Every control a run REQUESTED (`policy_override`, `admission_mode`, `force_worker`) must also be verified: `*_verified`
strictly true, no mismatched echoes in `control_observations`, and `control_unverified_turns` = 0. An unverified
varied control is a `treatment_problems` entry (reason `unverified`); an unverified non-varied requested control
makes the pair unproven. Controls requested in neither run need nothing.
`e3_least_loaded` vs `e3_prefix_then_load` is NOT a valid E2 pair.

 `e3_routing_mixed` under
`least_loaded` (spreads traffic so both workers build the prefix).
```bash
# Prerequisites (both restart the gateway; wait for /health 200 on 18080 afterwards):
#   make inference-controls-on      # the replay sends a policy override; with controls off the gateway does not echo it
#   ssh lambda 'sudo k3s kubectl -n inference-lab set env deploy/inference-gateway TENANT_MAX_CONCURRENCY=64'   # replay runs at concurrency 8; default 4 returns 429
source infra/inference/experiments/manifest/e2-prefix-reuse.env   # ONCE; sets RUN_ID=d123-<date>-e2-<HHMM>, do not re-source between arms
# 1. section 2 restart of BOTH workers, then the declared warm-up so only the PREFIX cache is cold:
python3 infra/inference/experiments/warmup.py --output-dir metrics/inference/$RUN_ID/warmup
# 2. cold run (metrics-url = one worker's window; repeat with 18002 if you need worker B)
make replay-e3-least-loaded TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID --label e2-cold"
# 3. reused run: NO restart in between, identical command
make replay-e3-least-loaded TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID --label e2-reused"
make inference-pull-range RUN_ID=$RUN_ID
```
Per-request evidence: cold vs reused TTFT (`e2_prefix_reuse`); window evidence: prefix-cache hit-rate delta from
`prometheus_window` (WINDOW-level, one worker). Note that within the cold run later conversations already hit
the prefix, so the comparison is "first pass vs fully warm", not "no cache vs cache".
For a fully cache-free reference restart before each run and use `--concurrency 1` with a single conversation.

### E3 Routing: least_loaded vs prefix_then_load

```bash
# Prerequisites: controls on and tenant quota 64, exactly as in E2 (make inference-controls-on; set env TENANT_MAX_CONCURRENCY=64).
source infra/inference/experiments/manifest/e3-routing.env   # ONCE; sets RUN_ID=d123-<date>-e3-<HHMM>, both arms share the folder
# restart both workers (section 2) before EACH run so both start with identical cache state
make replay-e3-least-loaded     TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
# restart again, then
make replay-e3-prefix-then-load TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
# optional SYNTHETIC large-prefix treatment: replay-e3-large-prefix-least-loaded / -prefix-then-load
make inference-pull-range RUN_ID=$RUN_ID
```
The replayer aborts (exit 2, no evidence) if the gateway does not echo the control, so a successful run already
proves `ALLOW_EXPERIMENT_CONTROLS=1`. Compare with `e3_compare` (checks manifests match and controls verified).

### E4 Admission on vs off

E4 shows `decode_slots` and `deadline_unachievable` (504, `timeout_queue`) firing with admission on versus off; KV
pressure is not forced. Prerequisites, in order (each gateway change restarts the pod: wait for `/health` 200 on 18080,
`worker_warm == 1` and `worker_ramp_cap == 0`):

**Tenant cap.** `TENANT_MAX_CONCURRENCY` applies PER TENANT, and `MAX_DECODE_SLOTS=8` is per worker (16 slots on two
workers). At the default 4, three tenants can hold only 12 in flight, so `decode_slots` can never fire, and
`tenant_interactive` (6 conversations in the trace) would itself be 429'd. E4 therefore runs with a cap of 10: interactive
(6) and batch (4) are fully admitted, `tenant_noisy` is capped at 10 of its 12 (two 429 `tenant_concurrency`), and up to 20
requests can be in flight against 16 slots. A uniform cap cannot show a strong tenant 429 and strong slot shedding together;
record the cap with the run. The cap is in `k8s/gateway/gateway.yaml` (`TENANT_MAX_CONCURRENCY=10`), so every deploy keeps it;
`make inference-refresh` applies it to a running cluster, and there is nothing to restore afterwards.

```bash
make inference-refresh              # running cluster -> current manifests (TENANT_MAX_CONCURRENCY=10 from gateway.yaml), Secrets, controls ON, verify workers; restarts the gateway
# then wait for worker_warm == 1 and worker_ramp_cap == 0 on both workers (a few minutes)
# admission values as deployed (MAX_DECODE_SLOTS=8; PREFILL_TOKENS_PER_S and QUEUE_WAIT_PER_WAITING_S are placeholders): note them with the run; measured values are read from the window (section 1a)
make inference-verify-workers       # args identical, exit 0
source infra/inference/experiments/manifest/e4-admission.env   # ONCE; sets RUN_ID=d123-<date>-e4-<HHMM>
export E4_START=$(date +%s)
make replay-e4-admission-off TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
# optional but cleaner: restart both workers (section 2) so the second arm starts with the same cache state
make replay-e4-admission-on  TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
export E4_END=$(date +%s)
make inference-pull-range RUN_ID=$RUN_ID START=$E4_START END=$E4_END
make inference-pull-evidence RUN_ID=$RUN_ID
# record the observed prefill rate / queue stats over the window (the three queries in section 1a, with W=$((E4_END-E4_START)) and &time=$E4_END)
make evidence-analyze RUN="metrics/inference/$RUN_ID/<off_run_dir> metrics/inference/$RUN_ID/<on_run_dir>" RANGE=metrics/inference/$RUN_ID/prometheus_range
```
`x-admission-mode: off` skips only capacity/deadline shedding; the tenant quota still runs. Pass condition: with
admission ON the gateway sheds with named reasons (`decode_slots` and/or `deadline_unachievable`) and the interactive
tenant is protected from `tenant_noisy`; with it OFF those sheds are absent. If both arms look identical, the
thresholds are too loose: return to section 1a, do not report the pair. Compare with `e4_compare`: sheds by reason,
`timeout_queue`, per-tenant Jain index, batch starvation (goodput stays 0 through the tunnel, so do not lead with it).
Record Grafana Gateway+admission and Queues for the same window. Check the scenario really sets interactive
deadlines (`deadline_ms` in `requests.jsonl` must not be null); otherwise the 504 path cannot fire.

### Soak and 10x demo load (arrival-rate mode)

For soak runs and the 10x load demonstration, use open-loop arrival-rate scheduling instead of pure closed-loop concurrency. The replayer schedules conversation dispatch according to an inter-arrival distribution (Poisson by default, or uniform) while maintaining the concurrency semaphore as a safety ceiling, keeping manifests, control verification, and per-request evidence intact without needing Locust:

```bash
# Run on the Lambda host against localhost:18080 (or locally through the SSH tunnel):
make replay-arrival-rate SCENARIO=e4_admission_overload RATE=5.0 DIST=poisson TARGET_URL=http://127.0.0.1:18080 \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID --label soak-poisson-5rps"
```

The manifest records `arrival_rate` and `arrival_distribution`, and per-request latencies and throughput reflect true open-loop queueing dynamics.

### E5 Recompute vs real KV hop (#133)

E5 asks at which prefix size moving KV from Worker A to Worker B beats recomputing it on B. It has two legs
that must be comparable, so run both in ONE session on the SAME worker image and bundle:

- **Control legs (no transfer):** `e5_local_reuse_<size>`, `e5_recompute_control_<size>` and
  `e5_destination_hit_<size>` for `1k 2k 4k 7k` (plus `e5_local_eviction_4k`), run with `make replay-e5`.
  A destination hit is B's OWN cache; it is not a hop.
- **Real-hop leg:** `make inference-kv-smoke`, which runs the four controlled cases per prefix size and writes
  `crossover.json`. Only this leg can show transferred tokens/bytes and destination consumption.

Do not compare a hop measured on the LMCache/Mooncake image against controls measured on the plain
image: the engine differs. Apply the KV bundle first, then run both legs on it (the bundle's
`cross_worker_recompute_transfer_disabled` case is the in-bundle recompute reference).

```bash
# 1. Build, push and render the bundle (nothing is applied by these). Fresh CACHE_NAMESPACE per session:
make inference-kv-image KV_IMAGE=<registry>/ai-inference-kv:0.11.0-lmcache0.3.9 && docker push <same tag>
make inference-kv-render KV_IMAGE=<same tag> CACHE_NAMESPACE=e5-$(date +%Y%m%d)-01 \
  TEMPLATE_VERSION=taxi-chat-v1 PREFIX_CONTRACT_VERSION=prefix-v1 OUT=work/kv-hop.yaml
# 2. Review work/kv-hop.yaml, sync, apply it on the isolated host (authorized GPU session only), wait for both
#    workers and the gateway to be healthy. The bundle enables forced placement and experiment headers.
make inference-sync
scp -i "$LAMBDA_SSH_KEY_PATH" work/kv-hop.yaml "$LAMBDA_SSH_USER@$LAMBDA_SSH_HOST:/tmp/kv-hop.yaml"   # after: source infra/inference/.env
ssh lambda 'sudo k3s kubectl apply -f /tmp/kv-hop.yaml && sudo k3s kubectl -n inference-lab rollout status deploy/inference-worker-a deploy/inference-worker-b deploy/inference-gateway --timeout=15m'
make inference-verify-workers      # both workers on the KV image, same args
# 3. Copy the examples (under infra/inference/mooncake/) to work/ and replace every placeholder from the deployed
#    cluster. topology "workers" must stay ["worker_a","worker_b"] (the emitted KV_WORKER_ID values) and the
#    namespace must equal the rendered one.
mkdir -p work && cp infra/inference/mooncake/topology.example.json work/kv-topology.json \
  && cp infra/inference/mooncake/versions.example.json work/kv-versions.json
source infra/inference/experiments/manifest/e5-recompute.env   # ONCE; sets RUN_ID=d123-<date>-e5-<HHMM>
# 4. Control legs, one scenario per size (needs make inference-controls-on, ALLOW_FORCED_PLACEMENT=1 and quota 64
#    as in E2; restart both workers per section 2 before each scenario so cache state is identical):
make replay-e5 E5_SCENARIO=e5_recompute_control_4k TARGET_URL=http://127.0.0.1:18080 \
  METRICS_URL=http://127.0.0.1:18002/metrics REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
#    repeat for e5_local_reuse_<size>, e5_destination_hit_<size> and each size 1k 2k 4k 7k
# 5. Real-hop leg, all four sizes in one run:
make inference-kv-smoke GATEWAY_URL=http://127.0.0.1:18080 WORKER_A_URL=http://127.0.0.1:18001 \
  WORKER_B_URL=http://127.0.0.1:18002 TOPOLOGY=work/kv-topology.json VERSIONS=work/kv-versions.json \
  PREFIX_SIZES="1k 2k 4k 7k" OUT=metrics/inference/$RUN_ID/kv-hop
make inference-pull-range RUN_ID=$RUN_ID
```

What counts as passing: `validation.json` is `valid` and `cases.json` holds all four cases for EVERY size
(`<case>@<size>`). The run fails closed if any case lacks positive tokens and bytes or
`destination_consumed: true` after the forward pass. A worker header or lower latency is not proof.
The smoke now sends warm-up requests per worker first (`--warmup-requests`, default 3; the first request's TTFT is
recorded as the cold figure in `manifest.json` under `warmup`), and alternates which comparison leg runs first by size
index (`leg_order` in `crossover.json`).

Before the first run, list which eviction counters the engine and the store expose
(`curl -s http://127.0.0.1:18001/metrics | grep -i evict`) and note them with the run; the gateway exports none
(see "Eviction and ghost entries" in `infra/inference/README.md`).

Reading `crossover.json` (one entry per size): `actual_reusable_tokens` is the real x-axis (the size label is
nominal); compare `recompute_ttft_ms` / `recompute_e2e_ms` with `transfer_ttft_ms` / `transfer_e2e_ms`, and read
`transfer_ms`, `lookup_ms`, `confirm_ms`, `transferred_bytes` for the cost of the hop. A TTFT is `null` unless
exactly one request landed on that worker during its window, so rerun with nothing else hitting the cluster.
Scopes: event fields are per-request; TTFT here is a one-request vLLM window, and the prefix-hit counter
deltas are WINDOW-level. The notebook's E5 cell currently shows only the control run (`E5_RUN`); read
`crossover.json` directly (or add a notebook cell) for the crossover until the notebook consumes it.

Report the crossover where it is: if the hop never wins at any measured size, or only above 7k, say so. The
size ceiling is the 8,192-token worker context. Keep the hardware in the write-up; a crossover from a
different GPU than E0-E4 is not directly comparable.

### Demo window and extra proofs (C6a, C6b, C8, C6)
Run these AFTER E4 and E5 so they cannot contaminate those comparisons. The runs here are demonstrations and proofs, not
A/B comparisons. Set one id for the whole block (`export RUN_ID=d123-$(date +%Y%m%d)-demo-$(date +%H%M)`), keep every output
under `metrics/inference/$RUN_ID/`, and note the epoch start and end of each part for `make inference-pull-range`.
Gateway state for all parts: `make inference-controls-on`, tenant cap 10 (`TENANT_MAX_CONCURRENCY=10`, as in E4; the demo reuses the E4 trace), workers warm. Each gateway change restarts the pod: wait for `/health` 200 and `worker_warm == 1`.

**C6a. One end-to-end run of the real app through `/serve`** (so the evidence includes the actual Track B agent loop, not only the replayer).
```bash
make app-serve-dev          # own terminal: LLM_PROVIDER=serve, gateway http://localhost:18080/serve; needs make mcp-dev (port 8001) running too
# ask one multi-step taxi question in the app (UI/API on port 8080) so it makes several tool/agent steps; note the time
ssh lambda 'sudo k3s kubectl -n inference-lab logs deploy/inference-gateway --tail=50000' > metrics/inference/$RUN_ID/gateway.log
make trace-request RUN_DIR=<run dir with the request> REQUEST_ID=<id> GATEWAY_LOG=metrics/inference/$RUN_ID/gateway.log
```
The app does not write a replayer `requests.jsonl`; take the request id from the gateway log (`x-request-id`) and say plainly in the
write-up that this is the only evidence from the real app (D2b).

**C6b. Queue and engine proofs (Part 5)** — read with the notebook queue cell (`queue_proof`); four small windows:
1. *Gateway queue forms:* a mixed short + long run above the slot count (for example the E0 sweep command at concurrency 16, or `replay-arrival-rate`). Expect non-zero `orch_replica_queue_depth` and `orch_queue_wait_seconds` by class, while `vllm:num_requests_waiting` stays near 0 (the gateway holds the queue because `WORKER_MAX_INFLIGHT` = `--max-num-seqs`).
2. *KV pressure:* with this model and 0.45 utilisation KV may never get close (E0 peaked at 46%). Do not force it; if it stays low, record "not reached" and show `kv_cache_usage_perc` and `num_preemptions_total`. Say whether `kv_pressure` shed or vLLM preempted only if it actually happened.
3. *Client abort frees KV:* start a long streaming request, kill the client mid-decode (Ctrl-C), then scrape `vllm:num_requests_running` and `kv_cache_usage_perc` for that worker before and after (a few seconds apart). Expect running back to its prior value.
4. *Worker restart ramp:* `make inference-restart`, then watch `worker_health`, `worker_warm` and `worker_ramp_cap` (expect 0 while cold, then 2, then 4, then released) and the traffic share to the returning worker while a steady load runs.

**C8. One demo window that fills every dashboard** — a single scripted pass; record the epoch start/end, then pull the range and screenshot Grafana.
```bash
export DEMO_START=$(date +%s)
# 1. Guard rejects (400/413): reasons malformed_payload, missing_messages, prompt_too_long, context_window_exceeded
make inference-serve-smoke
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18080/serve -H 'content-type: application/json' -d '[1]'                 # malformed_payload
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18080/serve -H 'content-type: application/json' -d '{"messages":[]}'      # missing_messages
# 2. Tenant 429, slot 503 (decode_slots), deadline 504, queue timeout: the E4 overload trace with admission ON
make replay-e4-admission-on TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
# 3. Overflow: same trace again with overflow enabled (only 503/529 limiter reasons leave; 429/500/slice_oom and 504 stay local)
make inference-overflow-check          # local; must pass first
make inference-overflow-on             # needs OVERFLOW_URL + the overflow-credentials Secret current (see below)
make replay-e4-admission-on TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID --label demo-overflow"
make inference-overflow-off
# 4. Real app: repeat the C6a question once while the cluster is loaded, so the app loop is in the mix
# 5. Hop: only if the KV bundle is applied (playbook E5 section, make inference-kv-smoke); otherwise leave the Hop panel empty and say so
export DEMO_END=$(date +%s)
make inference-pull-range RUN_ID=$RUN_ID START=$DEMO_START END=$DEMO_END
```
Screenshot (Grafana `http://127.0.0.1:13000`) for the same window: Cluster, Success and failures, Gateway + admission, Router,
Queues, vLLM, Hop, Overflow. Confirm each reject has its counter: `guard_reject_total{reason}`, `orch_shed_total{reason,class,code}`,
`queue_error_total{reason}`, `orch_overflow_total{reason,provider,model,outcome}`, `overflow_error_total{reason}`. A 429 or a 504 must
never appear as overflowed; check the Overflow dashboard for that.

*Before step 3 (overflow credentials; see also "Overflow" below):* the cluster Secret is created at `make inference-up` from `SUPERLINKED_API_KEY` in
`infra/inference/.env`. If the key changed since, run `make inference-secret` then `make inference-gateway-restart` (the pod reads the
Secret only at start). `OVERFLOW_URL` and `OVERFLOW_MODEL` (`Qwen/Qwen3.8-27B-FP8`) come from the same `.env`; overflow stays OFF for E4/E5.

**C6. Extra proofs the brief asks for**
- *Live overflow case:* from C8 step 3, pick one request with `x-overflow` set and one 429 that stayed local; keep both response headers and the `orch_overflow_total` scrape.
- *Queue-timeout case:* from the E4-on window, one `queue_error_total{reason="timeout_queue"}` event and its request in `requests.jsonl` (`x-queue-decision`).
- *Cold-restart to declared-warm timing (warmup time):* `make inference-restart`, note the restart time, the time `worker_warm` flips to 1 and the first successful warm probe (`warm_probe_total`); put the two numbers in `warmup_summary.json` or the write-up.

### Overflow (demo window only, not E4/E5)

Overflow is off by default and must stay off for E4/E5: it would turn their local 503s into overflowed requests.
Destination: Superlinked, `Qwen/Qwen3.8-27B-FP8` (same Qwen family, larger). Only 503/529 whose reason names a limiter
(`decode_slots`, `kv_pressure`, `no_signal`) leave; 429, 500 and `slice_oom` stay local.

```bash
# once: SUPERLINKED_API_KEY and OVERFLOW_URL (the OpenAI-compatible .../v1/chat/completions route) in infra/inference/.env
make inference-secret                  # creates the overflow-credentials Secret (also part of make inference-up)
make inference-overflow-on             # sets OVERFLOW_ENABLED/PROVIDER/MODEL/URL; restarts the gateway
# drive a 503 (saturate the slots, e.g. the E4 trace); overflowed responses carry x-overflow and x-overflow-reason
make inference-overflow-off            # restore the default
```
Check first, locally and with no cluster: `make inference-overflow-check` (models, chat, tools, stream; a few tokens of credit). Exit 0 means the route is OpenAI-compatible. Scrape `orch_overflow_total` /
`overflow_error_total` for the window.

### Memory proof

`make inference-pull-range RUN_ID=<id> START=<epoch> END=<epoch> STEP=15s` over the busiest window (the E0
sweep or E4 pair). The range export holds HBM used/free (DCGM), KV usage, running/waiting, request rate,
preemptions, prefix hit ratio, TTFT/queue quantiles and gateway queue depth/sheds/picks
(`infra/inference/observability/prometheus/evidence_queries.json`). `memory_proof` joins them and flags
flat-at-max, monotonic growth without frees, preemptions and idle windows. Flat HBM is normal when vLLM
preallocates; the KV series is the informative one.

## 4. Turning runs into the notebook

```bash
make evidence-analyze RUN="metrics/inference/<id>/<run_dir> ..." RANGE=metrics/inference/<id>/prometheus_range   # stdlib text summary
make evidence-notebook    # regenerates experiments/123_evidence.ipynb, opens Jupyter (uv project: experiments/)
```
In the notebook's first code cell fill `RUNS` (or set `EVIDENCE_RUNS_JSON`). Cells with no run directory print
"run directory not provided". Tests: `uv run --project services/app pytest tests/experiments -q` (stdlib, runs in
CI) and, with the notebook dependencies, `uv run --project experiments pytest tests/experiments -q`.

Evidence scopes in every output: **per-request** = requests.jsonl fields; **WINDOW-level** = Prometheus deltas and
range series. Report negative or neutral results as found (plan completion checklist).

## 5. After the session

Turn overflow off if it was enabled (`make inference-overflow-off`) and controls back off (`kubectl set env deploy/inference-gateway ALLOW_EXPERIMENT_CONTROLS=0` or
`make inference-deploy`). After E5, also restore the normal gateway and worker manifests (the KV bundle
enables forced placement and experiment headers; `make inference-deploy` resets them), and leave Mooncake
unexposed outside the lab. Run `make inference-pull-evidence RUN_ID=<id>` once more, then stop the tunnel and
the instance (`make inference-teardown` / Lambda console).

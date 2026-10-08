# Inference evidence run playbook (#123 E0-E5 + memory proof)

Ordered commands for a cluster session that produce the artifacts the local analysis toolkit
(`experiments/analysis`) and notebook (`experiments/123_evidence.ipynb`) consume. Background and
experiment meaning: [inference-testing-guide.md](inference-testing-guide.md) section 4 and
[inference-project-plan.md](../inference-project-plan.md) sections 8-9 and "Final-run evidence rules".
All commands run from the repository root (or the active worktree) unless noted.

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

## 1. Pre-flight checklist (once per session)

1. `make inference-tunnel` in a dedicated terminal (or `make inference-connect` to sync first).
2. Workers healthy and warm: `curl -s http://127.0.0.1:18001/health`, `:18002/health`, `:18080/health`;
   gateway `worker_warm` and `worker_health` are 1 for both workers (Grafana overview, or
   `curl -s http://127.0.0.1:19090/api/v1/query --data-urlencode 'query=worker_warm'`).
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
5. Schema/prefix check (manual): start the local MCP (`make mcp-dev`), read its dataset-schema resource and
   confirm the column list equals `CANONICAL_TAXI_SCHEMA["columns"]` in
   `services/app/app/benchmarks/canonical_prefix.py`. If it differs, update that file and run
   `uv run --project services/app pytest services/app/tests/test_scenarios.py` before any E3 run.
6. Tokenizer count: confirm `curl -s -X POST http://127.0.0.1:18080/tokenize -H 'content-type: application/json'
   -d '{"model":"Qwen/Qwen3-0.6B","prompt":"hello"}'` returns a `count`. The replayer writes the exact prefix
   token count into every manifest (`system_prefix.exact_tokens`; `null` plus a reason if unreachable).
7. Export the manifest env vars from section 0 and check the local toolchain:
   `uv run --project services/app pytest tests/experiments -q`.

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
# Restore before E4: `... set env deploy/inference-gateway TENANT_MAX_CONCURRENCY-` (trailing dash unsets it).
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

```bash
# Prerequisites: make inference-controls-on (also sets TENANT_ALLOWLIST), and restore the default tenant quota so admission can
# shed: ssh lambda 'sudo k3s kubectl -n inference-lab set env deploy/inference-gateway TENANT_MAX_CONCURRENCY-' (trailing dash unsets it).
# Tune admission thresholds so the trace actually overloads the workers; otherwise on and off look the same.
source infra/inference/experiments/manifest/e4-admission.env   # ONCE; sets RUN_ID=d123-<date>-e4-<HHMM>
make replay-e4-admission-off TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make replay-e4-admission-on  TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make inference-pull-range RUN_ID=$RUN_ID START=<epoch-before-first-run> END=<epoch-after-last-run>
```
Requires `TENANT_ALLOWLIST` (pre-flight step 4). `x-admission-mode: off` skips only capacity/deadline
shedding; the tenant quota still runs. Compare with `e4_compare`: goodput, p99, sheds by reason,
`timeout_queue`, per-tenant Jain index and batch starvation. Also screenshot/record the Grafana Gateway+admission
and Queues dashboards for the same window. Restart workers between runs if you want identical cache state.

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
# 3. Copy mooncake/topology.example.json and versions.example.json to work/kv-topology.json and
#    work/kv-versions.json; replace every placeholder from the deployed cluster. topology "workers" must stay
#    ["worker_a","worker_b"] (the emitted KV_WORKER_ID values) and the namespace must equal the rendered one.
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

Turn controls back off (`kubectl set env deploy/inference-gateway ALLOW_EXPERIMENT_CONTROLS=0` or
`make inference-deploy`). After E5, also restore the normal gateway and worker manifests (the KV bundle
enables forced placement and experiment headers; `make inference-deploy` resets them), and leave Mooncake
unexposed outside the lab. Run `make inference-pull-evidence RUN_ID=<id>` once more, then stop the tunnel and
the instance (`make inference-teardown` / Lambda console).

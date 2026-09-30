# Inference evidence run playbook (#123 E0-E4 + memory proof)

Ordered commands for a cluster session that produce the artifacts the local analysis toolkit
(`experiments/analysis`) and notebook (`experiments/123_evidence.ipynb`) consume. Background and
experiment meaning: [inference-testing-guide.md](inference-testing-guide.md) section 4 and
[inference-project-plan.md](inference-project-plan.md) sections 8-9 and "Final-run evidence rules".
All commands run from the repository root (or the active worktree) unless noted.

## 0. Conventions

- **Run id:** `RUN_ID=d123-<YYYYMMDD>-<experiment>` (for example `d123-20261001-e3`). Everything for one
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
export RUN_ID=d123-$(date +%Y%m%d)-e0
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
`e3_least_loaded` vs `e3_prefix_then_load` is NOT a valid E2 pair.

 `e3_routing_mixed` under
`least_loaded` (spreads traffic so both workers build the prefix).
```bash
export RUN_ID=d123-$(date +%Y%m%d)-e2
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
export RUN_ID=d123-$(date +%Y%m%d)-e3
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
export RUN_ID=d123-$(date +%Y%m%d)-e4
make replay-e4-admission-off TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make replay-e4-admission-on  TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make inference-pull-range RUN_ID=$RUN_ID START=<epoch-before-first-run> END=<epoch-after-last-run>
```
Requires `TENANT_ALLOWLIST` (pre-flight step 4). `x-admission-mode: off` skips only capacity/deadline
shedding; the tenant quota still runs. Compare with `e4_compare`: goodput, p99, sheds by reason,
`timeout_queue`, per-tenant Jain index and batch starvation. Also screenshot/record the Grafana Gateway+admission
and Queues dashboards for the same window. Restart workers between runs if you want identical cache state.

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
`make inference-deploy`), run `make inference-pull-evidence RUN_ID=<id>` once more, then stop the tunnel and
the instance (`make inference-teardown` / Lambda console).

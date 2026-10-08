# #123 cluster session runbook (one sitting)

Operator script for the first real evidence session. The detailed reference is `docs/inference-experiments/inference-run-playbook.md`
(merged in PR #153); this file is the ordered, checkbox version with checkpoints and triage. Pass criteria are the per-experiment checkpoints below and
the "Final-run evidence rules" in `docs/inference-project-plan.md`. Nothing in this sitting needs #133: the real-hop leg of E5 (merged in PR #154) is a separate GPU session on the KV bundle, described in the playbook's E5 section.

Time budget: about 3-4 hours of instance time if nothing breaks. Write down the run ids as you go.

---

## 0. Before you touch the cluster (local, 10 min)

- [ ] `cd` to the repo root (main checkout is fine; nothing here changes code), `git pull --rebase`, confirm HEAD includes PR #153.
- [ ] `uv run --project services/app pytest tests/experiments -q` passes (stdlib analysis toolkit).
- [ ] Optional but useful: `uv run --project experiments pytest tests/experiments -q` (slow first install; verifies the notebook environment, never verified yet).
- [ ] Run ids: sourcing an experiment file from `infra/inference/experiments/manifest/` (section below) sets `RUN_ID=d123-<date>-<exp>-<HHMM>` (local time), so every rerun gets its own folder and nothing is overwritten. Source the file **once per experiment** and do not re-source it between the two arms of a comparison (E2, E3, E4), or the arms land in different folders. `make inference-e1` sources it itself, so each run gets a new id. Pin with `D=<yyyymmdd> T=<HHMM> source ...`. Run ids below are `d123-$D-<exp>-<HHMM>`.
- [ ] Decide the manifest inputs once and reuse them for EVERY run (comparisons are void if they differ or are `unknown`):

```bash
# Simplest: the exports live in infra/inference/experiments/manifest/ (common.env + one file per experiment).
# In each experiment terminal, source that experiment's file; it loads common.env and sets RUN_ID:
#   source infra/inference/experiments/manifest/e1-warmup.env       # also: e0-capacity, e2-prefix-reuse, e3-routing, e4-admission, e5-recompute
# (the block below is the same content as common.env, kept for reference; keep both in sync)
export MODEL_REVISION=c1899de289a04d12100db370d81485cdf75e47ca      # worker --revision (k8s/workers/worker-a.yaml)
export TOKENIZER_REVISION=$MODEL_REVISION                           # confirmed in the vLLM 0.11.0 startup log (tokenizer_revision == revision)
export CHAT_TEMPLATE_REVISION=$MODEL_REVISION                       # ASSUMPTION: template ships with the tokenizer in the model repo
export ENGINE_FLAGS="--dtype bfloat16 --kv-cache-dtype auto --max-model-len 8192 --max-num-seqs 8 --max-num-batched-tokens 8192 --block-size 16 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes --gpu-memory-utilization 0.45"
export VLLM_VERSION=0.11.0                                          # image vllm/vllm-openai:v0.11.0
export KV_BLOCK_SIZE=16                                             # worker --block-size
export INFERENCE_TOPOLOGY="two vLLM replicas/HAMi slices on one physical A100"
# Verify these against the LIVE workers (read-only; prints image/args for both workers, whether they match, and the engine
# settings from each startup log: vLLM version, revision, tokenizer_revision, block_size, dtype, kv_cache_dtype, max_seq_len):
#   make inference-verify-workers
# Same exports must be set in EVERY terminal that runs make replay-* / run_scenario.py (they are not persisted).
# optional SLO overrides (defaults: TTFT 100 ms, E2E 3500 ms)
# export INTERACTIVE_TTFT_SLO_MS=100 E2E_SLO_MS=3500
```

## 1. Bring the cluster up (Lambda instance)

- [ ] Bring-up flow actually used (2026-10-04): launch the Lambda instance; put the new IP in `~/.ssh/config` (`Host lambda`) AND `LAMBDA_SSH_HOST` in `infra/inference/.env` (keep `INFERENCE_REMOTE_DIR='~/ai-analytics-inference'` quoted); `ssh lambda 'nvidia-smi -L'`; then from the repo root `make inference-sync`, `make inference-bootstrap`, `make inference-config`, `make inference-deploy` (long: workers pull image + model). `make inference-up`/`inference-connect` were not used. `make inference-status` does not exist.
- [ ] Dedicated terminal: `make inference-tunnel` (keep it open). Ports: gateway 18080, worker A 18001, worker B 18002, Prometheus 19090, Grafana 13000.
- [ ] `make inference-deploy` only if manifests/dashboards/alerts changed since the last deploy. This run needs the merged dashboards, alert rules and gateway, so do run it once if the cluster was last deployed before PRs #144-#153. Note: deploy resets gateway env to manifest defaults (controls OFF).
- [ ] `make inference-gateway-restart` if only gateway code changed (the gateway files ship as a ConfigMap).

### Fresh-instance rehearsal (planned after E1-E4)

To prove the whole process works on a new instance: launch it, put the new IP in `~/.ssh/config` (`Host lambda`) and `LAMBDA_SSH_HOST` in `infra/inference/.env`, then from the repo root:

```bash
make inference-fresh-up      # sync, bootstrap, config, deploy, controls on, verify workers (long; the deploy waits up to 15 min per worker)
make inference-tmux-start    # session with run / tunnel / watch windows
make inference-e1            # in the run window; then the other experiments
make inference-tmux-end      # when finished; also terminate the instance to stop billing
```

## 2. Pre-flight checks (stop and fix before any run)

- [ ] Health: `curl -s http://127.0.0.1:18001/health`, `:18002/health`, `:18080/health` all OK.
- [ ] Engine then serve smoke, in this order: `make inference-smoke` (each worker directly), then `make inference-serve-smoke` (gateway `/serve`: stage headers on an accepted request, one tool-calling agent step, oversized-prompt and over-window rejects). Stop on any failure.
- [ ] Gateway metrics: `curl -s http://127.0.0.1:18080/metrics | head` is non-empty; includes `worker_health`, `worker_warm`, `orch_admit_total`, `gateway_ttft_seconds`.
- [ ] Prometheus scrapes the gateway: `curl -s http://127.0.0.1:19090/api/v1/targets | grep -c inference-gateway` is at least 1 and the target is `up`.
- [ ] Both workers warm: `curl -s http://127.0.0.1:19090/api/v1/query --data-urlencode 'query=worker_warm'` shows 1 for worker_a and worker_b.
  - Also visible in Grafana: dashboard "Inference Lab / Router & Placement", panel "Worker warm (0 = healthy but cold)" shows `worker_a` and `worker_b` at 1.
  - Note: the gateway is scraped by two jobs (`kubernetes-pods` and `inference-gateway`), so the query returns 4 series. Filter with `{job="inference-gateway"}` when summing or counting.
- [ ] Grafana `http://127.0.0.1:13000`: open Overview, Gateway+admission, Router, Queues, Overflow, Memory Proof. Every panel that should show data does (live rendering has NEVER been checked). Note broken panels; fix queries in `infra/inference/observability/grafana/dashboards.py`, regenerate, redeploy before recording evidence.
- [x] Alerts: Prometheus `/alerts` lists the five rules (four required plus `InferenceEngineTTFTHigh`); confirmed loaded and Inactive on 2026-10-04 (the `--set-file` problem is fixed in `deploy.sh`). `promtool` is optional and skipped. Inactive does not prove a rule can fire: paste a rule's metric into Prometheus Graph to confirm it returns data once traffic flows.
- [ ] Tokenizer: `curl -s -X POST http://127.0.0.1:18080/tokenize -H 'content-type: application/json' -d '{"model":"Qwen/Qwen3-0.6B","prompt":"hello"}'` returns a `count`.
- [ ] Schema check (manual, affects E3 prefix length): first refresh the MCP venv if `make mcp-dev` fails with `ImportError: cannot import name 'aggregate_taxi_data'` (stale installed copy of `dataset_spike`): `uv sync --project services/mcp --reinstall-package dataset-spike`. Then `make mcp-dev` locally, read the `dataset://nyc-taxi/schema` resource, compare its `columns` to `CANONICAL_TAXI_SCHEMA["columns"]` in `services/app/app/benchmarks/canonical_prefix.py`. If different: update that file, run `uv run --project services/app pytest services/app/tests/test_scenarios.py`, and rerun before E3.
- [ ] Turn on the test-only controls (this triggers a gateway rollout):

```bash
make inference-controls-on    # ALLOW_EXPERIMENT_CONTROLS=1 + TENANT_ALLOWLIST; waits for rollout
# at the end of the session: make inference-controls-off
```
  Add `ALLOW_FORCED_PLACEMENT=1` only when you reach E5.
- [ ] Re-check gateway health and `worker_warm` after the rollout (snapshots reset on restart).

## 3. Session order and checkpoints

Do the runs in this order. After each: run the `evidence-analyze` line, glance at Grafana for the same window, write the run dir name in your log.

**The `evidence-analyze` line (run once per experiment, after the run; it needs a finished run dir, so not before the first run):**
```bash
make inference-pull-range RUN_ID=$RUN_ID            # optional; gives the Prometheus range series (needs the tunnel)
make evidence-analyze RUN="metrics/inference/$RUN_ID/<run_dir>" RANGE=metrics/inference/$RUN_ID/prometheus_range
# leave RANGE off for a per-request-only summary; pass two run dirs in RUN to compare a control/treatment pair
```
Read `comparable: true/false` and `manifest_check` first. Before this, export the manifest env vars (section 0) so runs are not `unknown`.

### E1 Warmup (cold vs declared-warm) — `RUN_ID=d123-$D-e1-<HHMM>`
**One command:** with `make inference-tunnel` running in another terminal, `make inference-e1` does everything below (loads the E1 manifest, asks to confirm, restarts workers B then A, waits for the tunnel to re-attach, runs the cold run, waits `WARM_WAIT_S` (default 300 s), runs the warm run, pulls evidence). Options: `WARM_WAIT_S=600`, `YES=1` to skip the prompt, `D=<yyyymmdd>` to pin the run date. It stops if the cold run measures nothing. The manual steps follow for reference.

**Known trap (hit on 2026-10-04):** the tunnel reaches each worker through `kubectl port-forward svc/...`, which dies when the pod is replaced and re-attaches about a second later. A cold run started right after the restart got `Remote end closed connection` from both workers and wrote `{}`; poll `/health` on 18001 and 18002 until 200 before step 2. Do not run the cold and warm runs back to back: the warm run's first request is then effectively the first request after the restart. Client-side TTFT here includes the SSH tunnel (about 250-350 ms floor), which can hide a small cold-start penalty.

0. Load the manifest inputs and `RUN_ID` for this experiment (in the terminal you will run E1 from; it prints nothing). Expect `d123-<date>-e1 16 0.11.0`:

   ```bash
   source infra/inference/experiments/manifest/e1-warmup.env
   echo $RUN_ID $KV_BLOCK_SIZE $VLLM_VERSION
   ```

1. Restart workers: `make inference-restart` (worker B) and `bash infra/inference/scripts/restart-test.sh inference-worker-a metrics/inference/$RUN_ID`.
2. Immediately: `python3 infra/inference/experiments/warmup.py --output-dir metrics/inference/$RUN_ID/cold`
3. After several minutes of normal operation: `python3 infra/inference/experiments/warmup.py --output-dir metrics/inference/$RUN_ID/warm`
4. `make inference-pull-evidence RUN_ID=$RUN_ID`
- Checkpoint: `cold/warmup_summary.json` TTFT for the first request is visibly higher than `warm` p50/p95. If not, the restart did not go cold (check pod restart time).

### E0 Capacity and first limiter — `RUN_ID=d123-$D-e0-<HHMM>`
0. Load the manifest inputs and `RUN_ID` for this experiment (in the terminal you will run E0 from; it prints nothing). Expect `d123-<date>-e0 16 0.11.0`:

   ```bash
   source infra/inference/experiments/manifest/e0-capacity.env
   echo $RUN_ID $KV_BLOCK_SIZE $VLLM_VERSION
   ```

1. Confirm the live workers match the manifest (read-only). Expect `args identical across workers: yes`, vLLM `v0.11.0`, `block_size: 16`, matching `revision`/`tokenizer_revision`, and `chunked_prefill_enabled=True`:

   ```bash
   make inference-verify-workers
   ```

2. `make inference-capacity RUN_ID=$RUN_ID` (synthetic, preliminary).
3. Application-shaped sweep (no policy override):
```bash
uv run --project services/app python services/app/scripts/run_scenario.py --scenario e3_routing_mixed \
  --endpoint-type gateway_chat --target-url http://127.0.0.1:18080 --output-dir metrics/inference/$RUN_ID \
  --sweep-concurrency 1,2,4,8,12,16 --label e0-sweep
make inference-pull-range RUN_ID=$RUN_ID
make evidence-analyze RUN="metrics/inference/$RUN_ID/<run_dir>" RANGE=metrics/inference/$RUN_ID/prometheus_range
```
- Checkpoint: `sweep.csv` shows goodput flattening or falling while throughput still rises (the knee). Token-rate columns may be `n/a` if the gateway does not return usage; that is expected, not an error. The memory range tells you whether KV, decode slots or queue hit first. Record the answer to "what limited this GPU for this app?".

### E2 Prefix reuse (cold vs reused) — `RUN_ID=d123-$D-e2-<HHMM>`
**Step 0:** load the manifest inputs and `RUN_ID` for this experiment (in the terminal you will run E2 from; it prints nothing). Expect `d123-<date>-e2 16 0.11.0`:

```bash
source infra/inference/experiments/manifest/e2-prefix-reuse.env
echo $RUN_ID $KV_BLOCK_SIZE $VLLM_VERSION
```

Same scenario and same policy in both runs; only cache state differs.
```bash
# restart BOTH workers (section E1 step 1), run the declared warm-up so only the prefix cache is cold:
python3 infra/inference/experiments/warmup.py --output-dir metrics/inference/$RUN_ID/warmup
make replay-e3-least-loaded TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID --label e2-cold"
# NO restart in between:
make replay-e3-least-loaded TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID --label e2-reused"
make inference-pull-range RUN_ID=$RUN_ID
```
- Checkpoint: the analysis reports `comparable: true` for the pair (same scenario hash, policy, SLOs, topology, revisions, flags, prefix size, concurrency; controls verified). If `comparable: false`, read `manifest_check` and fix the env/flags, then rerun. Remember: "first pass vs fully warm", not "no cache vs cache". Prefix-cache deltas are window-level, one worker.

### E3 Routing headline — `RUN_ID=d123-$D-e3-<HHMM>`
**Step 0:** load the manifest inputs and `RUN_ID` for this experiment (in the terminal you will run E3 from; it prints nothing). Expect `d123-<date>-e3 16 0.11.0`:

```bash
source infra/inference/experiments/manifest/e3-routing.env
echo $RUN_ID $KV_BLOCK_SIZE $VLLM_VERSION
```

Restart BOTH workers before EACH run so both start with identical cache state.
```bash
make replay-e3-least-loaded     TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
# restart both workers again, then:
make replay-e3-prefix-then-load TARGET_URL=http://127.0.0.1:18080 METRICS_URL=http://127.0.0.1:18001/metrics \
  REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make inference-pull-range RUN_ID=$RUN_ID
```
- Optional synthetic large-prefix treatment afterwards: `make replay-e3-large-prefix-least-loaded` / `replay-e3-large-prefix-prefix-then-load` (same pattern). It is a separate, clearly labelled experiment; the headline answer comes from `e3_routing_mixed`.
- Checkpoint: the replayer exits 2 immediately if the gateway does not echo the policy control. That means `ALLOW_EXPERIMENT_CONTROLS` is off: fix and rerun, nothing is wasted. With the real ~272-token prefix, `prefix_then_load` is EXPECTED to stick on turn 1 and spill to load-based placement from turn 2 (`placement_reason_by_turn` shows `prefix_affinity` then `prefix_overlap_low`). That is a finding, not a bug.

### E4 Admission on vs off — `RUN_ID=d123-$D-e4-<HHMM>`
**Step 0:** load the manifest inputs and `RUN_ID` for this experiment (in the terminal you will run E4 from; it prints nothing). Expect `d123-<date>-e4 16 0.11.0`:

```bash
source infra/inference/experiments/manifest/e4-admission.env
echo $RUN_ID $KV_BLOCK_SIZE $VLLM_VERSION
```

Needs `TENANT_ALLOWLIST` (set in pre-flight). Tune thresholds first so the trace really overloads two workers (`MAX_DECODE_SLOTS`, `KV_FREE_MIN`, `PREFILL_TOKENS_PER_S`, `QUEUE_WAIT_PER_WAITING_S` via `kubectl set env`); write down the values used.
```bash
make replay-e4-admission-off TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make replay-e4-admission-on  TARGET_URL=http://127.0.0.1:18080 REPLAYER_FLAGS="--output-dir metrics/inference/$RUN_ID"
make inference-pull-range RUN_ID=$RUN_ID START=<epoch-before-first-run> END=<epoch-after-last-run>
```
- Checkpoint: with admission off, interactive p99/goodput degrade under overload; with it on, sheds appear by reason (`kv_pressure`, `decode_slots`, `deadline_unachievable`, `timeout_queue`) and interactive goodput is preserved. Jain fairness across tenants and batch starvation are in `e4_compare`. Also watch the four alerts fire (queue/shed surge, KV pressure if reached) and record that. Note `x-admission-mode: off` skips only capacity/deadline shedding; tenant quota still runs.

### E5 Recompute control (no transfer) — `RUN_ID=d123-$D-e5-<HHMM>`
**Step 0:** load the manifest inputs and `RUN_ID` for this experiment (in the terminal you will run E5 from; it prints nothing). Expect `d123-<date>-e5 16 0.11.0`:

```bash
source infra/inference/experiments/manifest/e5-recompute.env
echo $RUN_ID $KV_BLOCK_SIZE $VLLM_VERSION
```

Needs `ALLOW_FORCED_PLACEMENT=1` on the gateway (add with `kubectl set env`, then rollout).
- Restart both workers before each scenario so earlier caches cannot leak (cold state matters here).
- Run the three cases at each size (1k, 2k, 4k, 7k): `e5_local_reuse_<size>` (A to A), `e5_recompute_control_<size>` (A to B, no transfer), `e5_destination_hit_<size>` (B warmed separately, then A-origin continuation to B), using `make replay-e5 E5_SCENARIO=<name>` (check the Makefile for the exact variables: target URL, metrics URL, output dir).
- `e5_local_eviction_4k` is inconclusive unless vLLM evidence shows eviction actually happened; report it that way.
- Checkpoint: forced turns are verified (`force_worker_verified: true`); a `control_not_applied` turn means forced placement was not honored. Sizes are chars/4 estimates; the exact token count is in each manifest (`system_prefix.exact_tokens`). If a 7k case is rejected (prompt + max_tokens over 8192), note it and drop to a smaller size.
- Real-hop treatment is NOT done here. The #133 code is merged, but the hop leg needs the KV bundle and `make inference-kv-smoke` (playbook E5 section), and the controls must be re-run on that same bundle for a like-for-like comparison. Do not claim a hop from a worker change or a latency drop.

### Single-request trace (once, after E3 or E4)
```bash
# pull the gateway log (pull-evidence does not include it):
ssh ... 'kubectl -n inference-lab logs deploy/inference-gateway --tail=50000' > metrics/inference/$RUN_ID/gateway.log
make trace-request RUN_DIR=metrics/inference/$RUN_ID/<run_dir> REQUEST_ID=<id from requests.jsonl> GATEWAY_LOG=metrics/inference/$RUN_ID/gateway.log
```
Pick one interactive request that was queued or placed interestingly. The trace shows guard+quota+admission as one span, placement, queue wait, hop status ("not attempted (#133)"), client-observed `post_first_token_ms` (NOT engine decode), and SLO met or not. Per-request engine prefill/decode is unavailable by design.

## 4. After the last run

- [ ] Turn controls off: `make inference-controls-off` (sets `ALLOW_EXPERIMENT_CONTROLS=0` and removes `ALLOW_FORCED_PLACEMENT`), or `make inference-deploy`.
- [ ] Take Grafana screenshots of each dashboard for the recorded windows (Overview, Gateway+admission, Router, Queues, Memory Proof).
- [ ] `make inference-pull-evidence RUN_ID=<id>` once more for each experiment run id (logs, hardware, pods).
- [ ] Note the gateway defaults you ran with (queue depth, inflight slots, timeouts, admission thresholds) and whether they need tuning.
- [ ] Stop the tunnel; `make inference-teardown` or the Lambda console to stop billing.

## 5. Turn the data into the deliverable

1. `make evidence-analyze RUN="..." RANGE=...` for each experiment; fill the notebook: `make evidence-notebook` then set `RUNS` in the first code cell.
2. Commit the notebook and selected raw snapshots (keep `metrics/` gitignored, copy only what the evidence index points to).
3. Fill `docs/inference-experiments/inference-evidence-index.md` rows with real artifact paths; change status from pending-run to available. The two hop rows stay merged-pending-live-run until a KV-bundle run retains passing `validation.json` and `crossover.json`.
4. Decide: prefix-identity follow-up (history-aware), calibration of gateway defaults and alert thresholds, and whether to approve E6 (needs a verified Dynamo version; see ADR 0011 and `python -m app.benchmarks.parity`).

## 6. Quick triage

| Symptom | Likely cause | Fix |
|---|---|---|
| Replayer exits 2 right away, "control not applied" | `ALLOW_EXPERIMENT_CONTROLS` off on the gateway | set env, wait for rollout, rerun |
| `comparable: false` with `unprovable_fields` | a manifest env var was unset (`unknown`) or differs between runs | export the same values for both runs, rerun |
| `comparable: false`, `treatment_problems` | same policy/admission twice, or control unverified | rerun with the intended different control |
| Goodput is 0 | TTFT unmeasured (non-streaming) or SLO too tight | use default `--gateway-stream`; check SLO flags |
| 400/413 from the gateway on long E5/E3 turns | prompt + `max_tokens` over 8192 | smaller size or lower `max_tokens` |
| No gateway series in Prometheus | gateway scrape target missing | check `values.yaml` job `inference-gateway`, redeploy |
| Alerts missing from Prometheus | alert values not nested under `serverFiles.alerting_rules.yml` (fixed in `deploy.sh`) | check the helm values built by `deploy.sh`; `/alerts` should list 5 rules |
| `worker_warm` stuck at 0 | worker still loading or snapshot stale | wait, check worker logs and gateway logs |
| Dashboard panel empty | metric not exported or label mismatch | compare with `evidence_queries.json` and `gateway/metrics.py` |
| Everything 503 `no_signal` | all snapshots stale/no healthy worker | check worker pods, gateway to worker connectivity |

## 7. Known unverified before this session

Live Grafana rendering; `promtool` and helm `--set-file` alert loading; gateway experiment controls on a live gateway;
the `experiments` uv environment; the pinned taxi schema vs the live MCP schema; exact token counts of the E5 prefixes
(7k may sit near the 8192 limit); playbook commands as a whole (written without cluster access). Expect a few fixes.

### Status after the 2026-10-04 pre-flight (uncommitted local fixes; push later as one PR)
- Resolved: helm alert loading (5 rules visible); gateway controls on a live gateway (`make inference-controls-on` rolled out, health and `worker_warm` OK); pinned taxi schema (`store_and_forward_flag` -> `store_and_fwd_flag` in `canonical_prefix.py` and `config/scenarios/e3_routing_mixed.json`; `test_scenarios.py` passes); MCP venv refresh (`uv sync --project services/mcp --reinstall-package dataset-spike`).
- Fixed in scripts: `deploy.sh` (alerts values, `~` in `--from-file=`), `gateway-restart.sh` (gateway-only rsync, `~` fix), new `controls.sh` + `make inference-controls-on/off`.
- New: `infra/inference/experiments/manifest/` (common.env + e0..e5 files).
- Still open: gateway and workers are scraped twice (annotations + static jobs), so unfiltered sums double-count; filter `job="inference-gateway"` / `job="inference-workers"`. Tenant quota (`other` bucket, max concurrency 4 by default) may 429 the E0 sweep above 4 concurrent; check `sweep.csv` for 429s. Dashboards mostly need traffic to confirm; `experiments` uv env and exact E5 token counts still unverified.
- Stale docs to fix in the same PR: `infra/inference/README.md:206`, `docs/work-history/0078-evidence-dashboards-alerts.md` lines 18 and 33, `docs/inference-experiments/inference-testing-guide.md:54`; needs a work-history entry.

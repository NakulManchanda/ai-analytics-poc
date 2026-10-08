# Work 0090 — Gateway hardening, step 6

## Milestone

Inference track — hardening before the E4 and E5 cluster runs (follows works 0086-0089)

## Branch

`feat/inference-hardening-step6` (from `main`, not stacked)

## Worktree

`.worktrees/inference-hardening-step6`

## Goal

Implement items A21, A7/A29, A28, and A33: notebook proof cell for gateway replica queue depth vs vLLM waiting per worker/class, trace acceptance for worker `kv_hop` events and live KV Hop dashboard/alert, realistic NYC TLC taxi domain prompts for E5 and smoke tests, and open-loop Poisson arrival-rate scheduling mode in the replayer for soak and 10x demos.

## Starting state

- Notebook lacked a proof cell showing gateway queue depth (`orch_replica_queue_depth`) vs vLLM waiting/running/preemptions per worker over an isolated range window (A21).
- `trace_request.py` and `trace.py` only recognized gateway stages (`stage == "hop"`) and missed worker `kv_hop` structured events (`event == "kv_hop"`); Grafana had only a placeholder `kv_hop_stub.json` without real panels or an integrity alert (A7, A29).
- E5 scenarios and smoke prompts used 8 generic repeating sentences with synthetic tags instead of realistic NYC TLC yellow taxi domain material (schema, tool contracts, retrieved observations) (A28).
- The replayer supported only closed-loop concurrency semaphores without open-loop arrival-rate scheduling for soak/10x load generation (A33).

## Changes

- **A21: Queue proof analysis module and notebook cell**
  - Created `experiments/analysis/queue.py` with `queue_proof()` that digests Prometheus range queries for `orch_replica_queue_depth`, `orch_queue_wait_p95`, `vllm:num_requests_waiting`, `vllm:num_requests_running`, and `vllm:num_preemptions_total`.
  - Added range fixtures `orch_replica_queue_depth.json` and `orch_queue_wait_p95.json` in `tests/experiments/fixtures/prometheus_range/`.
  - Updated `experiments/build_notebook.py` to add `plot_queue()`, `QUEUE` cell, and `QUEUE_RANGE` run configuration; regenerated `experiments/123_evidence.ipynb`.
- **A7 & A29: Worker `kv_hop` trace correlation, Grafana dashboard, and Prometheus alert**
  - Updated `services/app/app/benchmarks/trace.py` to accept structured worker events (`event == "kv_hop"` or `stage == "hop"`), correlates `router_prefix_id` from placement headers, and auto-discovers worker log files.
  - Added `--worker-log` flag and discovery in `services/app/scripts/trace_request.py`.
  - Replaced `kv_hop_stub.json` with a full, real `kv_hop.json` dashboard generated from `infra/inference/observability/grafana/dashboards.py` (panels for `hop_total` by result and reason, error rates, transferred tokens and bytes, transfer and confirm durations, and eviction/preemption tracking).
  - Added `InferenceHopIntegrity` alert rule to `infra/inference/observability/prometheus/alerts.yaml`.
  - Updated `infra/inference/tests/test_dashboards_contract.py` to assert dashboard metric contracts and alert definitions.
- **A28: Realistic NYC TLC yellow taxi material for E5 and smoke prompts**
  - Replaced generic repeating filler sentences in `services/app/app/benchmarks/e5_locality.py` with `REAL_TAXI_SECTIONS`: canonical dataset schema, pinned Parquet specifications, governed MCP analytics tool contracts, and authentic retrieved DuckDB observations (top pickup zones, hourly volume, borough metrics).
  - Preserved exact token calculation (`want = target_tokens * 4 - _SYSTEM_WRAP`) and regenerated all 13 scenario files (`config/scenarios/e5_*.json`).
  - Updated `infra/inference/mooncake/smoke.py` prompt padding with real TLC taxi schema and DuckDB observation text, preserving nominal token estimates.
- **A33: Open-loop arrival-rate mode in scenario replayer**
  - Added `arrival_rate` and `arrival_distribution` (`poisson` default, or `uniform`) to `ScenarioReplayer` in `services/app/app/benchmarks/replayer.py`. In rate mode, conversation dispatches are scheduled according to exponential inter-arrival times while retaining the concurrency semaphore as a safety ceiling.
  - Added fields to `ReplaySummary` and CLI arguments `--arrival-rate` and `--arrival-distribution` in `services/app/scripts/run_scenario.py`, recording them in `manifest.json`.
  - Added `replay-arrival-rate` target to `Makefile`.
  - Documented arrival-rate usage for soak and 10x demos in `docs/inference-experiments/inference-run-playbook.md`.

## Key decisions

- Worker log events lack `router_prefix_id`; `trace.py` correlates it from gateway placement records or request headers to produce confirmed hop status.
- Exact token control in `e5_locality.py` is preserved by using plain ASCII domain text without unescaped quotes, guaranteeing that JSON system wrapper token arithmetic matches the vLLM prefix cache bucket sizes (1k, 2k, 4k, 7k).
- Open-loop arrival-rate scheduling uses standard library `random.expovariate(arrival_rate)` to model a true Poisson arrival process without third-party dependencies such as Locust.

## Verification

- `pytest tests/experiments infra/inference/tests services/app/tests -q` — 776 passed, 1 skipped (nbclient in app venv).
- `pytest services/app/tests/test_e5_locality.py services/app/tests/test_replayer.py services/app/tests/test_trace.py -q` — 73 passed.
- `pytest infra/inference/tests/test_dashboards_contract.py infra/inference/tests/test_kv_hop_smoke.py -q` — 25 passed.
- `ruff check` clean on all touched code and scripts.
- Generated `dashboards/kv_hop.json` and regenerated all 13 `config/scenarios/e5_*.json` files; confirmed drift tests pass.

## Pull request

Draft PR #159 to `main`.

## Known limitations

- Live cluster evaluation of `kv_hop` dashboard and alert requires deploying the Mooncake KV bundle on the Lambda host during the scheduled GPU session.
- Arrival rate schedules conversation starts; multi-turn conversations proceed through subsequent turns with turn-level delay settings.

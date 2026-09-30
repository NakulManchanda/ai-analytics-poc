# Work history 0077 — #123 Slice A: measurement harness (goodput, decision headers, run manifest)

## Goal

First slice of #123: make every experiment measurable and reproducible before running any of them. Report throughput and goodput together and keep per-request evidence separate from window-level aggregates.

## Starting point

- The Slice D' replayer measured TTFT and tokens and Prometheus deltas, but had no SLO/goodput, no tenant/class/deadline inputs, no capture of the #122 gateway decision headers, and no run manifest.
- The gateway replay path was non-streaming, so it had no TTFT.

## Decisions

- Scenario turns/conversations gain optional `workload_class`, `tenant_id`, `deadline_ms`, `prefix_id`, sent as `x-request-priority`, `x-tenant-id`, `x-deadline-ms`, `x-prefix-id`.
- Per-request records keep wall-clock timestamps and the raw values of the ten gateway decision headers (also on 429/503/504).
- `goodput.py`: good = success AND TTFT <= SLO AND E2E <= deadline. Defaults TTFT 100 ms and E2E 3500 ms, overridable (flags/env). TTFT clause applies to interactive traffic only; batch is judged on success and E2E.
  Summaries: requests/s, tokens/s, good requests/s, good tokens/s, TTFT/E2E/queue-wait p50/p95/p99, breakdowns by class, tenant, worker, policy, and decision counts.
- Gateway replay now streams by default (`--gateway-stream`): TTFT = first non-empty content chunk, E2E = stream end, mid-stream error events and non-SSE error responses are failures. A request with no TTFT is not good unless `--allow-missing-ttft`.
- `run_scenario.py` writes a per-run directory (`requests.jsonl`, `summary.json`, `sweep.json`, `sweep.csv`, `manifest.json`); `--sweep-concurrency` produces offered load vs throughput vs goodput rows.
- Manifest records scenario hash, policy label, SLOs, topology (default: two vLLM replicas/HAMi slices on one physical A100), model/tokenizer/template revisions (env, else `unknown`), engine flags, times, and an `evidence_scope` note; Prometheus deltas are labeled window-level and never attributed to single requests.

## Verification

- `uv run --project services/app pytest services/app/tests tests -q` — 316 passed; `pytest infra/inference/tests -q` — 106 passed; black/ruff clean at CI scope.
- Not verified: against a live gateway (streaming format checked with mocks and by reading the gateway code); sweep is by concurrency only, no arrival-rate sweep.

## PR / merge state

Draft PR (this change). Later #123 slices: dashboards and alerts, E3/E4 scenarios, real runs, proofs.

## Lessons

- A goodput metric that silently reads 0 because TTFT is unmeasured is worse than no metric; measure TTFT on the path the experiments use.

## Review follow-up

Independent review found four issues; all fixed with tests.

- **Truncated streams counted as success:** a stream ending without `data: [DONE]` is now `incomplete_stream` (failure, never good).
- **Chunk count used as token count:** removed. `tokens_in`/`tokens_out` are `None` when the gateway reports no usage; `tokens_per_s` and `good_tokens_per_s` use measured usage only, and `tokens_unmeasured` (successful requests without usage) is reported in summaries and sweep rows. Request-rate and goodput metrics are unaffected. `stream_options.include_usage` stays on.
- **No correlation id:** each gateway turn sends a unique `x-request-id`; the returned header (else the sent id) is stored as `request_id` in `requests.jsonl`.
- **Sweep exit status:** `run_scenario.py` now exits nonzero if any sweep level had a failed turn, not just the last.

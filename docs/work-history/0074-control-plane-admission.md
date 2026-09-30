# Work history 0074 — Control plane Slice 2: admission and tenant quotas (#122)

## Goal

Second of four #122 slices: reject work the fleet cannot serve now, before placement, and protect tenant fairness.

## Starting point

- Slice 1 (PR #144) placed requests across Worker A/B but accepted everything that passed the guard.
- Before the first metrics scrape the gateway returned 503 `no_healthy_worker`.

## Decisions

- Pipeline is guard -> tenant quota -> `should_shed` -> place -> proxy; a shed request never calls a worker (tested).
- Reason taxonomy and status: `no_signal`, `kv_pressure`, `decode_slots` -> 503; `deadline_unachievable` -> 504;
  `tenant_tokens`, `tenant_concurrency` -> local 429, flagged `never_overflow` for Slice 4. Evaluation order: no_signal, kv_pressure, decode_slots, deadline.
- Deadline estimate = least-queued eligible worker's waiting x per-waiting wait + est tokens / prefill tokens-per-second, vs `x-deadline-ms`.
- Every shed result carries the exact snapshot inputs; the decision log records them plus the exact `tenant_id`.
- Tenants: sliding-window token budget and concurrency cap per allowlisted tenant; unknown tenants share one `other` bucket (also the only non-allowlist metric label). Leases release on shed, error, completion, and stream end.
- Metrics: `orch_admit_total`, `orch_shed_total{reason,class,code}`, `gateway_request_duration_seconds{stage,class}`, `orch_tenant_total`.
- Lifespan does one awaited snapshot refresh so the first request no longer 503s at startup.
- Thresholds are env-configurable defaults (`MAX_DECODE_SLOTS`, `KV_FREE_MIN`, `PREFILL_TOKENS_PER_S`, `QUEUE_WAIT_PER_WAITING_S`, tenant vars) to be calibrated in #123.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests -q` — 150 passed.
- `black --check` / `ruff check` (CI scope) — clean.
- Not verified: live cluster behavior and threshold calibration.

## PR / merge state

Draft PR (this change). Slices 3 (queues, `timeout_queue`) and 4 (overflow) remain.

## Lessons

- With no healthy worker, `no_signal` from admission now fires before placement's `no_healthy_worker`.

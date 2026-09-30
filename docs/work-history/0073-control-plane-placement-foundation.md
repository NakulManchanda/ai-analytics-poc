# Work history 0073 — Control plane Slice 1: placement foundation (#122)

## Goal

First of four #122 slices (placement foundation -> admission -> gateway queues -> overflow): replace the
single-worker pass-through gateway with a Worker A/B registry, guard, placement policies, and bounded metrics.

## Starting point

- `infra/inference/gateway/main.py` proxied to one `DEFAULT_WORKER_URL` with a hard-coded `x-place-decision: worker_a`, no `/metrics`.

## Decisions

- Pure modules `guard.py`, `placement.py`, `workers.py`, `metrics.py` (prometheus_client) so policy is unit-testable without a GPU.
- Policies: `round_robin`, `least_loaded` (waiting weighted 2x, so queue depth participates), `p2c`, `prefix_then_load`
  (sticky if prefix overlap >= 0.8 and in-flight tokens < 10k, spills at >= 70% KV used). Rules mirror the instructor reference router.
- Forced worker only with `ALLOW_FORCED_PLACEMENT=1`. `hop` intent exists but is never produced until #133 confirms transfer.
- Stale snapshots (>5s) dropped while a fresher worker exists; if all healthy workers are stale, conservative fallback is flagged and counted;
  no healthy worker fails closed with 503 `no_healthy_worker`.
- Prefix locality keyed by `x-prefix-id` as a time-bounded belief; high-cardinality ids only in the JSON decision log.
- Gateway modules use package-then-flat imports because the ConfigMap mounts the gateway dir flat.

## Verification

- `uv run --project services/app pytest infra/inference/tests -q` — 55 passed.
- `ruff check` / `ruff format --check` on changed gateway and test files — clean.
- Not verified: live two-worker cluster run (no deploy in this slice).

## PR / merge state

Draft PR (this change). Slices 2-4 remain: admission, gateway queues, overflow.

## Lessons

- `make infra-test` runs Terraform tests; the gateway tests need the direct pytest command.

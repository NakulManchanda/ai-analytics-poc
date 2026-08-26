# 0039 — Run State Model Expansion and POST /api/runs/{run_id}/cancel Endpoint

## Goal
Implement the core cancellation state model and the `POST /api/runs/{run_id}/cancel` endpoint (Issue #74 / Track 1 of Milestone v3). Set a fast Redis cancellation flag (`run:cancel:{run_id}`), update durable run status to `cancel_requested`, and emit `run.cancel_requested` over the event stream.

## Starting Point
The run lifecycle supported only `received`, `in_progress`, `completed`, `failed`, and `budget_exceeded`. There was no mechanism to signal cancellation or interrupt active runs via the API.

## Decisions
- Added `request_cancellation(run_id)` and `is_cancelled(run_id)` to `OrchestrationLoop` in `services/app/app/orchestration/loop.py`.
- Added `POST /api/runs/{run_id}/cancel` route in `services/app/app/routers/runs.py` returning `202 Accepted` with `CancelRunResponse(run_id, status="cancel_requested")`, `404` for unknown runs, and `409` for terminal runs.
- Set Redis fast-flag `run:cancel:{run_id}` with 300-second TTL to enable sub-millisecond cancellation checks.
- Added comprehensive unit tests in `services/app/tests/test_runs_cancel_api.py`.

## Verification
- `uv run --project services/app pytest services/app/tests`: 106 passed.
- `make test`: all backend, MCP, dataset spike, infra, and frontend tests passed.

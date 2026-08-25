# 0030 — v1.1 integration and verification boundary

## Goal

Close the v1.1 integration/documentation checkpoint for issue #49 after the
four bounded implementation tracks merged, without extending the architecture,
creating AWS resources, or claiming a deployment that did not occur.

## Starting point

- Branch: `codex/v11-integration`
- Base: `main` at `60373f3` (`v1.1: render truthful durable UI state (#53)`)
- Reviewed implementation PRs: #50 (`84c298f`), #51 (`cff3d3b`), #52
  (`f55d331`), and #53 (`60373f3`)

## Decisions

- Add one deterministic cross-route FastAPI smoke rather than another service
  or Compose harness. It exercises two blocking `/api/ask` turns, one
  backend-owned conversation ID, two runs and steps, conversation reload, and
  reconstructed SSE fields.
- Keep `InMemoryStateRepository` explicit in the local test. A fresh FastAPI
  app/TestClient reuses that injected repository to prove API reconstruction
  independently of first-app/event-publisher memory. This does not mean a new
  process can recover in-memory state.
- Keep actual ECS restart/DynamoDB recovery as a post-deployment operator
  checkpoint. No Terraform, AWS resource, deploy, or v2 streaming work belongs
  to this issue.
- Refresh user-facing runbooks to distinguish verified local behavior from the
  pending deployed checkpoint.

## Verification

- Red test established before completion: `uv run pytest
  tests/test_v11_integration_smoke.py -q` first exposed the contract/schema
  mismatches while the new smoke was being aligned with the existing API.
- Final focused test: `uv run --project services/app pytest
  services/app/tests/test_v11_integration_smoke.py -q` — 1 passed.
- `uv run ruff format --check tests/test_v11_integration_smoke.py` and `uv run
  ruff check tests/test_v11_integration_smoke.py` — passed.
- `make test` — 88 app, 1 MCP, 13 dataset-spike, 18 infrastructure/script, and
  18 web tests passed; `npm run build` and Python `compileall` passed.
- `make integration-smoke` — existing isolated five-service Compose smoke
  passed and cleaned up its task-owned Compose project.

## Follow-ups from the four-track review

- #54 — make stale browser conversation reload pointers visible and testable.
- #55 — cover unavailable telemetry and partial conversation snapshots.

Neither is a blocker for the v1.1 integration contract.

## PR and merge state

- Draft PR: [#56](https://github.com/NakulManchanda/ai-analytics-poc/pull/56)
  (`Closes #49`).
- Merge/deployment state: not merged; no deployment or AWS restart-recovery
  checkpoint performed.

## Lesson

Durable API reconstruction and process-restart durability are related but
separate assertions. Local tests should demonstrate the former honestly and
leave the latter to a deployed DynamoDB-backed operational checkpoint.

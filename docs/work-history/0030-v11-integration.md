# 0030 — v1.1 integration and verification boundary

## Goal

Close the v1.1 integration/documentation checkpoint for issue #49 after the
four bounded implementation tracks merged, without extending the architecture,
creating AWS resources, or making unverified deployment claims.

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
- Keep actual ECS restart/DynamoDB recovery as a separately recorded operator
  checkpoint. No Terraform, AWS resource, v2 streaming work, or deployment
  action belongs to this issue.
- Refresh user-facing runbooks to distinguish verified local behavior from the
  separately recorded deployed checkpoint and its remaining configuration gap.

## Verification

- Red test established before completion: `uv run pytest
  tests/test_v11_integration_smoke.py -q` first exposed the contract/schema
  mismatches while the new smoke was being aligned with the existing API.
- Final focused test: `uv run --project services/app pytest
  services/app/tests/test_v11_integration_smoke.py -q` — 1 passed.
- The integration smoke asserts reconstructed deterministic query context
  (`query_id`, `row_count`, and the sanitized tool observation) and terminal
  input/output/total tokens, cost, phase latencies, and blocking TTFT.
- `uv run black --check tests/test_v11_integration_smoke.py` and `uv run ruff
  check tests/test_v11_integration_smoke.py` — passed.
- `make test` — 88 app, 1 MCP, 13 dataset-spike, 18 infrastructure/script, and
  18 web tests passed; `npm run build` and Python `compileall` passed.
- `make integration-smoke` — existing isolated five-service Compose smoke
  passed and cleaned up its task-owned Compose project.

## Deployed checkpoint evidence

- `main` commit `60373f3` was deployed as `ai-app` ECR tag `60373f3` on task
  definition revision `:5`.
- The browser conversation `conv_a34fd065718e4128` completed two turns with
  runs `run_b39787a58f474bfa` and `run_922d92bc68494693`. Typing before the
  second question retained the completed first-run timeline.
- AWS CLI DynamoDB inspection showed the conversation metadata, four ordered
  messages, two run-index records, and completed metadata plus four completed
  steps for each run.
- ECS replaced task `70b9eed...` with `c4b5133...` on the same task-definition
  revision. A fresh Chrome tab after replacement restored the same conversation,
  all four messages, the latest run, six reconstructed SSE events, and truthful
  telemetry/TTFT.

The DynamoDB-backed deployment/restart/recovery checkpoint therefore passed.
The CloudWatch-clean criterion did not: `REDIS_URL` was absent, so the app used
`redis://localhost:6379/0` and logged connection-refused publish/read errors.
Follow-up [#57](https://github.com/NakulManchanda/ai-analytics-poc/issues/57)
tracks that configuration gap; the v1.1 tag remains unsatisfied until it is
resolved and CloudWatch is clean.

## Follow-ups from the four-track review

- #54 — make stale browser conversation reload pointers visible and testable.
- #55 — cover unavailable telemetry and partial conversation snapshots.

Neither is a blocker for the v1.1 integration contract.

## PR and merge state

- Draft PR: [#56](https://github.com/NakulManchanda/ai-analytics-poc/pull/56)
  (`Closes #49`).
- Merge state: not merged.
- Deployment/restart/DynamoDB recovery: passed as recorded above.
- CloudWatch-clean and v1.1-tag state: pending #57; not satisfied.

## Lesson

Durable API reconstruction and process-restart durability are related but
separate assertions. Local tests should demonstrate the former honestly and
leave the latter to a deployed DynamoDB-backed operational checkpoint.

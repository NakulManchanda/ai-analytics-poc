# Work 0021 — Milestone 12 local integration hardening and multi-service smoke

## Goal

Validate end-to-end multi-service orchestration across all containerized services (`web`, `app`, `mcp`, `worker`, `redis`) under isolated, automated test runners with non-conflicting ephemeral ports and deterministic smoke verification.

## Starting state

`origin/main` at `5fed1b5` has Milestones 0–11 merged (including Milestone 10 UI context inspector and Milestone 11 async background worker).

## Decisions

- Created automated end-to-end integration smoke script `scripts/smoke/12_local_integration.sh` verifying all 5 core scenarios:
  1. Service health and status verification through Nginx reverse proxy gateway.
  2. Synchronous AI analytics queries (`POST /api/ask`).
  3. Asynchronous job submission (`POST /api/jobs`) and background worker execution (`worker` container).
  4. Historical SSE run events fallback replay (`GET /api/runs/{job_id}/events`).
  5. React Web UI production bundle delivery and asset proxying.
- Added `make integration-smoke` target to root `Makefile`.
- Hardened cross-container job status resolution and event streaming in `app` and `worker` services when operating with in-memory state repositories during local development.

## Verification

- `scripts/smoke/12_local_integration.sh` executed cleanly against isolated Docker Compose stack with all 5 scenarios passing.
- Full project test suite `make test` passed across all services (102 backend tests + 10 frontend React vitest tests).
- Static formatting (`black`), linting (`ruff`), and bytecode compilation verified.

## Pull request and merge state

Branch `feat/m12-integration-hardening` tracks [issue #28](https://github.com/NakulManchanda/ai-analytics-poc/issues/28).

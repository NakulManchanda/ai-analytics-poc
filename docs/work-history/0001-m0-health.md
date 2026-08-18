# Work 0001 — Milestone 0 health endpoint

## Goal

Deliver the smallest runnable FastAPI application with an exact `GET /health` contract.

## Starting state

The bootstrap commit contained governance and planning files only. Branch `feat/m0-health` was
created in its own worktree from commit `3a5d4d4`.

## Decisions

- Use an application factory with a dedicated health router.
- Keep service dependencies and their lockfile under `services/app`.
- Use port 8080 consistently for local development, smoke checks, and the container.
- Pin uv 0.9.26 in the image and run the service as non-root UID 10001.
- Add no MCP, React, Redis, AWS, or LLM implementation.

## TDD evidence

The `TestClient` contract test was written first. Its RED run failed during collection with
`ModuleNotFoundError: No module named 'app'`. After the minimal application and route were added,
the focused test passed.

## Verification

- Focused health test: passed
- Full pytest suite: passed
- Black formatting check: passed
- Ruff lint check: passed
- Python compile/import check: passed
- Live `make dev` plus `make smoke`: passed
- Docker image build: passed
- Docker runtime user: `appuser` (`uid=10001`)
- Container `GET /health`: `200` with `{"status":"ok","service":"ai-app"}`

## Pull request and merge

Pending.

## Lessons

A real boundary test was sufficient to drive this milestone; no supporting infrastructure was
needed.

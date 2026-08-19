# Work 0018 — Milestone 9 Redis Streams and SSE integration

## Goal

Add transient execution fan-out over Redis Streams (`run-events`), streaming `GET /api/runs/{run_id}/events` SSE endpoint with heartbeat and state recovery, and wire event emission into the bounded agent loop.

## Starting state

`origin/main` at `d4b6746` has Milestones 0–8 merged, including bounded agent loop and execution budgets.

## Decisions

- Added disposable Redis sidecar in `docker-compose.yml` with in-memory configuration (`--save "" --appendonly no`).
- Implemented versioned event models and serializers in `app/events/models.py` (`RunEvent`).
- Implemented `RedisEventPublisher` and `InMemoryEventPublisher` in `app/events/publisher.py`.
- Emitted full lifecycle events (`run.received`, `context.loading`, `llm.started`, `llm.completed`, `tool.requested`, `tool.started`, `tool.completed`, `context.reduced`, terminal `run.completed|run.budget_exceeded|run.failed`) from `OrchestrationLoop`.
- Implemented streaming SSE endpoint `GET /api/runs/{run_id}/events` in `app/routers/events.py` with automatic state reconciliation from `StateRepository` if Redis stream is expired or disconnected.

## Verification

- `make test` passed across all suites (92 total tests: 62 app tests including 5 events & SSE tests, 1 MCP test, 13 dataset spike tests, 12 infra/port tests, 4 web tests).
- Clean linting (Ruff), formatting (Black), and bytecode compilation (`compileall`).

## Pull request and merge state

Branch `feat/m9-redis-sse` tracks [issue #21](https://github.com/NakulManchanda/ai-analytics-poc/issues/21).

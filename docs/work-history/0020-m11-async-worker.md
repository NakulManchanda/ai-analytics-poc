# Work 0020 — Milestone 11 async job submission and background worker

## Goal

Implement asynchronous background execution: `POST /api/jobs` endpoint accepting prompt queries, `GET /api/jobs/{job_id}` status query, Redis Streams queue (`async-jobs`), and standalone background worker process executing the bounded agent loop.

## Starting state

`origin/main` at `ac52559` has Milestones 0–9 merged. Milestone 10 is in PR #37.

## Decisions

- Added `Job` domain model in `app/state/models.py` (`job_id`, `status`, `prompt`, `conversation_id`, `run_id`, `created_at`, `completed_at`, `failure_code`).
- Added Job methods in `DynamoDBStateRepository` and `InMemoryStateRepository`.
- Implemented `POST /api/jobs` (returns 202 Accepted) and `GET /api/jobs/{job_id}` in `app/routers/jobs.py`.
- Implemented `JobQueueProducer` and `JobQueueConsumer` in `app/jobs/` using Redis Stream `async-jobs`.
- Implemented background worker engine in `app/worker.py` polling `async-jobs` and running `OrchestrationLoop`.
- Added `worker` service to `docker-compose.yml`.

## Verification

- `make test` passed across all suites (97 total tests: 67 app tests including 9 async job & worker tests, 1 MCP test, 13 dataset spike tests, 12 infra/port tests, 4 web tests).
- Formatting (Black), linting (Ruff), and bytecode compilation passed cleanly.

## Pull request and merge state

Branch `feat/m11-async-worker` tracks [issue #27](https://github.com/NakulManchanda/ai-analytics-poc/issues/27).

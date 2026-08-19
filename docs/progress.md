# Current milestone

Milestone 11 — asynchronous job submission and background worker

## Status

IN PROGRESS — [issue #27](https://github.com/NakulManchanda/ai-analytics-poc/issues/27)

## Merged milestone baseline

- **Milestones 0–10**: Merged (`128de6c`), including Redis Streams SSE and bounded-context visualization.
- **Milestone 13 Foundation**: Terraform infrastructure foundation and budget alerts merged.

## Acceptance criteria

- [x] `Job` domain model and DynamoDB state repository methods defined.
- [x] `POST /api/jobs` (202 Accepted) and `GET /api/jobs/{job_id}` endpoints implemented.
- [x] Redis Streams queue producer/consumer (`async-jobs`) implemented.
- [x] Background worker engine implemented in `services/app/app/worker.py`.
- [x] Docker Compose `worker` service added.
- [x] Full test suite passing across all services (`make test`).

## Next milestone

Milestone 12 (local integration hardening, #28) gates on Milestone 11 merging.

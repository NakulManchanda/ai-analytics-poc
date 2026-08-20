# Current milestone

Milestone 12 — local integration hardening & multi-service smoke

## Status

IN PROGRESS — [issue #28](https://github.com/NakulManchanda/ai-analytics-poc/issues/28)

## Merged milestone baseline

- **Milestones 0–11**: Merged (`5fed1b5`), including async job worker, Redis Streams SSE, and bounded-context visualization.
- **Milestone 13 Foundation**: Terraform infrastructure foundation and budget alerts merged.

## Acceptance criteria

- [x] End-to-end integration smoke script `scripts/smoke/12_local_integration.sh` verifying all 5 services.
- [x] Scenarios verified: service health gateway, sync `/api/ask`, async `/api/jobs` worker execution, historical SSE event replay, and React UI static assets.
- [x] Ephemeral non-conflicting host ports and isolated Compose projects.
- [x] `make integration-smoke` target added to root Makefile.
- [x] Full test suite passing across all services (`make test`).

## Next milestone

Milestone 14 (backend ECS deployment, #29) gates on Milestone 12 merging.

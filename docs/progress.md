# Project Status

## v1.1 durable state and truthful UI integration

The four implementation tracks are merged on `main`:

- #50 — stable SSE lifecycle (`84c298f`)
- #51 — application-owned durable conversation orchestration (`cff3d3b`)
- #52 — truthful durable run telemetry (`f55d331`)
- #53 — durable conversation UI (`60373f3`)

Issue #49 is the final integration and documentation checkpoint. Its focused
local API smoke uses the default `InMemoryStateRepository` and verifies one
backend-created conversation across two blocking `/api/ask` turns: four ordered
messages, two distinct runs with steps, a fresh FastAPI app/TestClient reload,
and durable SSE reconstruction with the expected content type, sequence,
working context, and terminal telemetry.

This local check proves reconstruction over the same explicitly injected local
repository. It does **not** prove a brand-new process preserves in-memory state,
and it does not claim deployed ECS restart recovery. The post-merge deployment
checkpoint is to deploy the v1.1 app/frontend with the existing
`DYNAMODB_TABLE_NAME`, complete two turns, replace/restart the ECS application
task, and reload/replay the recorded conversation and run.

The v1.1 integration review logged two non-blocking follow-ups: #54 (stale
conversation pointers) and #55 (unavailable telemetry and partial snapshots).

## Historical baseline

Milestones 0–16 and prior public-UAT work remain historical baseline work. Any
previous live-deployment statements are not v1.1 deployment evidence; use the
current local and public UAT guides for this release's verification boundaries.

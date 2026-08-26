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

The local check proves reconstruction over the same explicitly injected local
repository; by itself it does **not** prove process persistence. The separate
deployed checkpoint has now passed: `60373f3` was deployed as `ai-app` ECR tag
`60373f3` on task definition `:5`; DynamoDB inspection confirmed one
four-message/two-run conversation with completed steps; and a replacement ECS
task restored the same conversation, six reconstructed SSE events, telemetry,
and TTFT in a fresh Chrome tab. Details are recorded in work history 0030.

The user confirmed the deployed/manual checkpoint, and release commit `2febb216`
is tagged `v1.1-foundation-truthful-state`. The unconfigured Redis path is not a
v1.1 blocker; issue #57 now belongs to v2 transient streaming delivery.

The v1.1 integration review logged two non-blocking follow-ups: #54 (stale
conversation pointers) and #55 (unavailable telemetry and partial snapshots).

## v2 streaming text and live event delivery

The v2 live streaming text milestone is merged on `main`:

- #57 — provision ElastiCache Redis for transient delivery (PR #63, `6ad927b`)
- #61 — genuine provider text streaming over live run-first SSE (PR #62, `f536f54`)

Issue #61 delivers the run-first `POST /api/runs` lifecycle returning `202 Accepted`
with immediate `run_id`, live SSE token delta streaming via Bedrock `converse_stream`,
truthful provider TTFT latency metrics, progressive React frontend rendering, and
enhanced high-contrast sample query chips. Live Redis Streams coordinate in-flight delivery
while DynamoDB durably owns completed conversation and run step state.

## v3 cancellable runs and fast abort

The v3 cancellable runs milestone is delivered and merged on `main`:

- #74 — Run state model expansion & `POST /api/runs/{run_id}/cancel` (PR #78, `3dbcfa7`)
- #75 — Orchestration loop cancellation checkpoints & Bedrock stream abort (PR #79, `3c3e375`)
- #76 — Frontend cancellation controls, stop button, and timeline cancel badges (PR #80, `10464c4`)
- #77 — Integration smoke checks, documentation update, and deployed verification

Milestone v3 provides:
1. Fast cooperative cancellation through Redis `run:cancel:{run_id}` fast-flag (sub-millisecond lookup).
2. Checkpoints before every step (context loading, LLM proposal, MCP tool call, context reduction, final answer synthesis).
3. Immediate interruption of Bedrock token streaming on cancellation detection.
4. Clean partial text preservation in durable conversation state marked with `[interrupted]` and `interrupted: True`.
5. Emitted `run.cancel_requested` and `run.cancelled` lifecycle events on Redis Streams and SSE with full partial token telemetry.
6. React frontend Stop button and visual warning badges in the Timeline Inspector.

## Historical baseline

Milestones 0–16 and prior public-UAT work remain historical baseline work. Any
previous live-deployment statements are not v1.1 deployment evidence; use the
current local and public UAT guides for this release's verification boundaries.


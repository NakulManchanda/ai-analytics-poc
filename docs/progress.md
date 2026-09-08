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

## v4.1 voice input: WebSocket and continuous chunking

The Milestone v4.1 voice input pipeline is merged on `main`:

- #100 — Audio plumbing, WebSocket endpoint `/ws/voice`, and Amazon Transcribe streaming (PR #101, `5406fce`)
- #102 — React AudioWorklet capture, waveform visualizer, and transcript input (PR #103, `fe5debe`)

Milestone v4.1 delivers:
1. Bidirectional WebSocket `/ws/voice` accepting signed 16-bit linear PCM mono @ 16kHz (~100ms / 3200-byte frames) and JSON stop controls.
2. Official `amazon-transcribe` streaming integration for AWS mode, plus deterministic `FakeSTTProvider` for zero-cost offline tests.
3. React Web Audio capture downsampling microphone audio to 16kHz PCM chunks via `useVoiceInput`.
4. Live 16-bar frequency audio `WaveformVisualizer` animated by microphone volume energy.
5. Interactive `🎤 Voice` button with recording pulse animation and manual `⏹ Done Speaking` stop control.
6. Delivery of final speech transcripts directly into the query prompt textarea for review, refinement, and execution.
7. Local `docker-compose.aws.yml` override supporting real Bedrock and Amazon Transcribe with `make local-aws-compose`.


## Historical baseline

Milestones 0–16 and prior public-UAT work remain historical baseline work. Any
previous live-deployment statements are not v1.1 deployment evidence; use the
current local and public UAT guides for this release's verification boundaries.


## Demo cost control

Terraform park/resume support preserves the deployed architecture while avoiding
continuous backend charges between tests. Read [the runtime runbook](demo-runtime.md)
and [next-session status](next-session-handoff.md) before starting AWS testing.

## Shared Bedrock application allowance

Issue #88 adds a configurable USD 5.00 monthly UTC allowance for this
application's Nova Micro invocations. It uses a durable atomic reservation
before every blocking or streaming call and fails closed if the shared budget
cannot be safely authorized. The default local stack remains fake; an explicit
local real-Bedrock mode requires portable `DYNAMODB_TABLE_NAME` and AWS profile
configuration. This is an application guardrail, not an AWS billing cap.

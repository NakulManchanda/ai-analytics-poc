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
6. Delivery of final speech transcripts directly into the query prompt textarea for review, refinement, and execution, with continuous speech listening across pauses until user clicks Done Speaking (Issue #104).
7. Reliable credential resolution for Amazon Transcribe in Docker environments via boto3 StaticCredentialResolver.
8. Local `docker-compose.aws.yml` override supporting real Bedrock and Amazon Transcribe with `make local-aws-compose`.


## Historical baseline

Milestones 0–16 and prior public-UAT work remain historical baseline work. Any
previous live-deployment statements are not v1.1 deployment evidence; use the
current local and public UAT guides for this release's verification boundaries.

## Isolated inference-course track

Issue #120 and PR #130 defined the boundary for an isolated Lambda/k3s inference lab. A
real Lambda A100 k3s cluster with two `Qwen/Qwen3-0.6B` vLLM workers was launched and run on
2026-09-27/28 (see `docs/work-history/0063-lambda-inference-cluster.md`). #120, #121, #115
(including the tool-calling and `inference-pull-evidence` fixes) and #122 are merged and closed.
#123 (evidence matrix) has all its code and docs merged (PRs #148-#153), and its first cluster
session ran E0-E3 plus the memory proof on 2026-10-04/05 (see the Inference Track list below and
`docs/work-history/0084-123-cluster-runs-e0-e3-results.md`); E4, E5 and a fresh-instance
rehearsal still need a new instance. #133 (real cross-worker KV transfer) code is merged (PR #154); it stays open until a live two-worker GPU run retains passing proof artifacts. This track
does not change the AWS-only product deployment boundary; see `docs/inference-project-plan.md`
and ADR 0010.


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

## Observability O1: local OTEL and Jaeger trace skeleton

Issue #116 is active in draft PR #117. The first bounded observability slice
adds an opt-in, fail-open OpenTelemetry SDK path to the FastAPI application, a
safe root `ai.run` span for synchronous `/api/ask` runs, and a local Docker
Compose overlay containing an OTEL Collector and Jaeger.

`make observability-smoke` now proves the full local path on isolated dynamic
ports: request → `ai.run` → OTLP/HTTP Collector ingestion → batched OTLP/gRPC
export → Jaeger v3 query API. The smoke also verifies the run/conversation/model
attributes and confirms the prompt is absent from trace JSON.

This slice intentionally preserves CloudWatch EMF/JSONL metrics and does not yet
instrument the worker, Redis propagation, MCP, DuckDB, Bedrock child spans,
generic HTTP RED metrics, logs, Langfuse, Grafana/Prometheus, or AWS trace
export.

## Inference Track

- **#120 — Real-GPU vLLM Cluster Foundation**: Merged via PR #131 (`7c2c26c`). Created isolated `infra/inference/` bundle on Lambda with two symmetric `Qwen/Qwen3-0.6B` workers, HAMi 50/50 GPU slicing (20 GiB per worker), ClusterIP isolation, Prometheus/Grafana/DCGM observability, fail-closed restart recovery, and empirical capacity sweeps under `metrics/inference/run-20260927_215112/`.
- **#121 — Owned Serve Path Wiring & Prefix Contract**: Merged via PR #132 (`f1b3be9`). Implemented `ServeLLMClient` in `services/app/app/llm.py` forwarding model calls through gateway `/serve`, established Prefix Token Contract (`prefix.py`, `docs/prefix-contract.md`), request correlation headers (`x-prefix-id`, `x-agent-step`, etc.), thin remote gateway service (`infra/inference/gateway/`), K8s manifests, and comprehensive anti-bypass/MCP isolation tests. Direct follow-up commits on `main` added a local serve Compose overlay (`6b08d68`: `docker-compose.serve.yml`, `make local-serve-compose` / `local-serve-refresh`), a 60s timeout increase plus `make local-serve-app` (`2d3515c`), and voice disabled / `STT_PROVIDER` configuration in the serve overlay (`275b169`, `e303aa9`). Known issue: `6b08d68`'s retry in `ServeLLMClient` silently strips `tools`/`tool_choice` on HTTP 400 because the worker manifests lack the vLLM `--enable-auto-tool-choice`/`--tool-call-parser` flags; this must be fixed before #115 measurements.
- **Evidence-matrix acceptance gate**: `bf140e5` added the Section 15 evidence-matrix acceptance gate to `docs/inference-project-plan.md` and fit the E5 prefix sizes to the 8,192-token worker context (1K/2K/4K/7K). The #120 run `metrics/inference/run-20260927_215112/` referenced above is not present locally and could not be recovered from the remote; its numbers survive only in `docs/work-history/0063-lambda-inference-cluster.md`. A final cluster snapshot plus the Prometheus TSDB archive were pulled locally (gitignored) to `metrics/inference/lambda-final-20260928/` before the instance reset; Worker B was healthy at pull time, and an apparent outage was a stale local port-forward after its pod was recreated. The Lambda instance is being torn down 2026-09-28 to save cost, so the next session starts from a fresh instance.
- **#123 — Evidence matrix, cluster session 1 (2026-10-04/05)**: harness, scenarios, controls, analysis toolkit and notebook were merged earlier (PRs #148-#153); the first real Lambda A100 / k3s session then ran E0 (capacity and saturation), E1 (cold vs declared-warm), E2 (prefix reuse) and E3 (`least_loaded` vs `prefix_then_load`) plus the memory proof, and the instance was torn down. E4 (admission), E5 and the fresh-instance rehearsal are not run and need a new instance. Findings: the first limiter is the 8-slot scheduler cap, not KV (46% peak); a warm prefix cache cut turn-1 TTFT about 3x (cold 2,132 ms vs reused 681 ms); prefix-aware routing was a trade-off (worse turn-1 p95, better later turns). Limits: replay through the SSH tunnel, 10-11 s runs, one run per arm, empty warm-up summaries in E3. Details and open items: `docs/work-history/0084-123-cluster-runs-e0-e3-results.md`. The raw evidence is gitignored (`metrics/inference/`), so it is not in the repository.
- **#133 — Real KV transfer implementation**: merged via PR #154. It adds a backend-neutral identity
  and lifecycle contract, an opt-in LMCache 0.3.9/Mooncake connector for the existing vLLM 0.11.0
  HAMi workers, bounded hop metrics, and a retained four-case proof runner that can sweep the E5
  prefix sizes and writes `crossover.json`. Static verification passes. No current Lambda GPU instance is available, so the issue remains open until the runner
  captures positive Mooncake bytes/tokens and post-forward destination consumption on Worker B.

# Next Session Handoff

Last updated: 2026-09-08 (America/Toronto)

## Start here

The AWS demo is intentionally **parked** (`demo_enabled = false`) to control cost. Read
[the runtime runbook](demo-runtime.md) before any AWS testing.

Infrastructure state has been successfully migrated to private, versioned S3 storage with
native S3 locking. All intermediate infrastructure, telemetry, and voice input tasks are complete:
- **Issue #89 / PR #97**: Remote Terraform state in S3, GitHub OIDC release workflow, and DynamoDB GSI.
- **Issue #95 / PR #96**: Telemetry collection via CloudWatch EMF, local JSONL sink, and Streamlit comparison dashboard (`make dashboard`).
- **Milestone v3.1 / PR #94**: Real-time streaming character-by-character answer rendering via `EventSource` (tagged `v3.1-streaming-answer-eventsource`).
- **Milestone v4.1 / PRs #101, #103, #106**: Real-time voice input pipeline: WebSocket `/ws/voice`, Amazon Transcribe Streaming, React AudioWorklet + WaveformVisualizer, continuous listening across speech pauses until Done Speaking, and boto3 Docker credential resolution.

The next milestone is **Milestone v5 — Voice Output & Streaming Text-to-Speech (TTS)**.

---

## Current repository state

- `main` includes:
  - PR #101: WebSocket `/ws/voice` endpoint and Amazon Transcribe streaming backend.
  - PR #103: React AudioWorklet capture, waveform visualizer, and prompt transcript integration.
  - PR #106: Continuous speech listening across pauses, boto3 Transcribe credential resolution, and CI markdown path filtering.
- Release tags: `v1`, `v1.1-foundation-truthful-state`, `v2`, `v2-streaming-text`, `v3`, `v3-cancellable-runs`, `v3.1-streaming-answer-eventsource`. Candidate tag for current milestone: `v4-voice-input`.
- Dedicated worktrees: Clean. Main checkout is in sync with `origin/main`.

---

## Verified AWS state

- **Remote S3 State**: State bucket `s3://ai-analytics-poc-tfstate-107207236011/production/terraform.tfstate` is active, versioned, encrypted, and protected with native S3 locking (`production/terraform.tfstate.tflock`). Local state file is 0 bytes (delegated to S3).
- **DynamoDB Table**: `ai-analytics-poc-demo-application-state` has the `entity_type-started_at-index` Global Secondary Index in `ACTIVE` status.
- **GitHub Actions OIDC**: IAM Role `arn:aws:iam::107207236011:role/ai-analytics-poc-demo-github-actions-terraform` is active and linked to the repository's `terraform-production` environment.
- **Zero Drift**: `terraform plan` confirms 0 differences (`No changes. Your infrastructure matches the configuration.`).
- **Runtime Parking**: `demo_enabled = false`. ECS service/tasks, ALB, and Redis remain absent while parked to keep costs zero.
- **Frontend**: CloudFront serves `https://ai.sibkaro.com/` with HTTP 200, returning uncached JSON 503 for `/api/*` while demo backend is parked.

---

## Terraform Release & Execution Workflows

You can manage Terraform in two ways:

1. **Directly from GitHub Actions (Recommended for Releases)**:
   ```sh
   make tf-dispatch REF=main DEMO=false
   # Or for a specific tag with demo resumed:
   make tf-dispatch REF=v4 DEMO=true
   ```
   This dispatches `.github/workflows/terraform-release.yml` with OIDC credentials, shows plan summary counts in CI logs, and applies the exact commit.

2. **Locally via `make`**:
   Ignored local configuration `infra/terraform/backend.hcl` is configured to point to the remote S3 bucket:
   ```sh
   make -C infra/terraform plan
   ```
   Native S3 locking ensures local and CI runs never conflict.

---

## Local Development & Telemetry Dashboard

- **Standard fake LLM mode**: `make dev`, `make mcp-dev`, `make smoke`, `make compose-smoke`.
- **Real local AWS mode (opt-in Bedrock + Transcribe)**:
  ```sh
  export DYNAMODB_TABLE_NAME=ai-analytics-poc-demo-application-state
  export AWS_PROFILE=default
  make local-aws-compose
  ```
- **Metrics Comparison Dashboard**:
  ```sh
  make dashboard
  ```
  Launches the Streamlit + DuckDB dashboard querying `./metrics/*.jsonl` (or downloaded CloudWatch logs) to benchmark TTFT, tool execution latency, and Bedrock costs across models and prompts.

---

## Next roadmap milestone: Milestone v5 (Voice Output & Streaming TTS)
 
Reference document: `ai_analytics_poc_realtime_multimodal_plan.md` (Section 8: v5 — Voice Output).

### Objectives for Milestone v5:
1. **Streaming TTS Integration**: Amazon Polly streaming (or equivalent provider) integrated into the application server.
2. **Chunking & Sentence Buffering**: Buffer LLM `answer.delta` tokens into sentence boundaries and dispatch to streaming TTS without waiting for the full response to finish.
3. **Audio Frame Streaming**: Stream synthesized audio chunks (PCM / MP3) to the browser over WebSocket or binary SSE channel.
4. **Browser Audio Playback**: Web Audio API player queueing and playing speech seamlessly in real time as chunks arrive.
5. **Latency Waterfall & Telemetry**: Measure and expose TTFA (Time to First Audio):
   - STT finalize -> Tool latency -> LLM TTFT -> TTS TTFA.
6. **(Optional Pre-step)**: Tag `v4-voice-input` on `main` to mark the completed voice input milestone.

---

## Copy-ready next-session prompt

```text
Read AGENTS.md, docs/next-session-handoff.md, docs/demo-runtime.md, docs/progress.md, and ai_analytics_poc_realtime_multimodal_plan.md.

The AWS backend is currently parked (demo_enabled=false). Milestone v4.1 Voice Input is complete on main (WebSocket /ws/voice, Amazon Transcribe Streaming, React AudioWorklet + WaveformVisualizer, continuous speech listening across pauses, and CI markdown ignore filter).

We are starting Milestone v5 — Voice Output & Streaming Text-to-Speech (TTS). Follow the change workflow in AGENTS.md: create an issue, use a dedicated branch and project-local worktree (.worktrees/<topic>), push early, and open a draft PR. Build the smallest coherent vertical slice for sentence-buffered streaming TTS playback before full-duplex barge-in. Stop and wait for confirmation before resuming or deploying to AWS.
```

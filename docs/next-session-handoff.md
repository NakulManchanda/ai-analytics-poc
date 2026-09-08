# Next Session Handoff

Last updated: 2026-09-08 (America/Toronto)

## Start here

The AWS demo is intentionally **parked** (`demo_enabled = false`) to control cost. Read
[the runtime runbook](demo-runtime.md) before any AWS testing.

Infrastructure state has been successfully migrated to private, versioned S3 storage with
native S3 locking. All intermediate infrastructure and telemetry tasks are complete:
- **Issue #89 / PR #97**: Remote Terraform state in S3, GitHub OIDC release workflow, and DynamoDB GSI.
- **Issue #95 / PR #96**: Telemetry collection via CloudWatch EMF, local JSONL sink, and Streamlit comparison dashboard (`make dashboard`).
- **Milestone v3.1 / PR #94**: Real-time streaming character-by-character answer rendering via `EventSource` (tagged `v3.1-streaming-answer-eventsource`).

The next milestone is **Milestone v4 — Voice Input & Streaming Speech Processing**.

---

## Current repository state

- `main` includes:
  - PR #94: Streaming character-by-character answer rendering via `EventSource`.
  - PR #96: CloudWatch EMF stdout logs, local `./metrics/runs.jsonl` sink, and Streamlit comparison dashboard (`make dashboard`).
  - PR #97: Remote Terraform state in S3, GitHub Actions OIDC release workflow (`make tf-dispatch`), and DynamoDB GSI `entity_type-started_at-index`.
- Release tags: `v1`, `v1.1-foundation-truthful-state`, `v2`, `v2-streaming-text`, `v3`, `v3-cancellable-runs`, `v3.1-streaming-answer-eventsource`.
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

## Next roadmap milestone: Milestone v4 (Voice Input & Multimodal)

Reference document: `ai_analytics_poc_realtime_multimodal_plan.md` (Section 6: Milestone v4 — Realtime Voice Input & Audio Streaming).

### Objectives for Milestone v4:
1. Microphone capture in React frontend via Web Audio API / MediaRecorder (16kHz PCM / opus).
2. Audio streaming ingestion endpoint in FastAPI backend (`POST /api/runs/audio` or WebSocket/streaming chunked upload).
3. Speech-to-text transcription via Amazon Transcribe streaming or Bedrock speech capabilities.
4. Voice turn lifecycle telemetry (Speech-to-Text latency, Speech-to-First-Token latency) emitted to CloudWatch EMF and local JSONL.
5. End-to-end integration and smoke verification.

---

## Copy-ready next-session prompt

```text
Read AGENTS.md, docs/next-session-handoff.md, docs/demo-runtime.md, docs/decisions/0007-telemetry-metrics-comparison-architecture.md, and ai_analytics_poc_realtime_multimodal_plan.md.

The AWS backend is currently parked (demo_enabled=false). Remote Terraform state is fully migrated to S3 (ai-analytics-poc-tfstate-107207236011), the DynamoDB telemetry GSI is active, and GitHub OIDC dispatch is operational via `make tf-dispatch`.

We are starting Milestone v4 — Realtime Voice Input & Audio Streaming. Follow the change workflow in AGENTS.md: create an issue, use a dedicated branch and project-local worktree, push early, and open a draft PR. Build the smallest coherent vertical slice for microphone audio capture and streaming transcription before adding speech playback. Stop and wait for confirmation before resuming or deploying to AWS.
```

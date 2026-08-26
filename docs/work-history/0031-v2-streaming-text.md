# 0031 — v2 streaming text and live answer events

## Goal

Advance issue #61 from the blocking v1.1 answer lifecycle to genuine provider-streamed text over
live run-first SSE. Persist the complete assistant answer after successful streaming and expose
truthful TTFT without adding voice, cancellation, or durable Redis state.

## Starting point

- Branch: `codex/v2-streaming-text`
- Worktree: `.worktrees/v2-streaming-text`
- Base: `origin/main` at `2febb216` (`v1.1: verify durable state integration boundary (#56)`)
- Release baseline: annotated tag `v1.1-foundation-truthful-state`
- `POST /api/ask` synchronously runs the complete orchestration loop and returns only the final
  answer.
- `GET /api/runs/{run_id}/events` exposes SSE, but live delivery depends on Redis and durable
  fallback primarily reconstructs terminal runs.
- The final Bedrock answer call uses the blocking Converse response and produces one complete
  `LLMResult`; no real provider delta contract or measurable streaming TTFT exists.

## Decisions

- Use the run-first lifecycle from `ai_analytics_poc_realtime_multimodal_plan.md`: persist the run,
  return `202 Accepted`, and allow SSE to connect before orchestration completes.
- Emit `answer.delta` only from genuine provider-streamed chunks. Never simulate streaming by
  splitting a completed string.
- Keep the application responsible for the LLM stream, budgets, event order, and durable final
  message. DynamoDB remains authoritative; Redis issue #57 supplies transient delivery only.
- Keep cancellation, voice, STT, TTS, Pipecat, and multimodal inputs outside this issue.

## TDD and verification

Pending. Each contract will record its failing test before implementation, followed by focused
backend/frontend suites, production build, exact-head CI, and independent review.

## PR and merge state

- Issue: [#61](https://github.com/NakulManchanda/ai-analytics-poc/issues/61)
- Draft PR: pending first coherent commit.
- Deployment and tag: separate post-merge milestone checkpoint.


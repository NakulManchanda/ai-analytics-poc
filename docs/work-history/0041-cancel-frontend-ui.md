# 0041 — Frontend Cancellation Controls, SSE Badging, and Interrupted Run UI

## Goal
Implement Track 3 of Milestone v3 (Issue #76): add cancellation controls and SSE event visualizers to the frontend UI, enabling analysts to cancel in-flight queries and inspect cancelled run telemetry.

## Starting Point
Track 1 (PR #78) and Track 2 (PR #79) implemented the backend cancel API (`POST /api/runs/{run_id}/cancel`), loop abortion checkpoints, Bedrock streaming cutoff, durable state persistence (`[interrupted]`), and `run.cancel_requested` / `run.cancelled` SSE events. The React UI needed visual controls and lifecycle event handling.

## Decisions
- **`TimelineInspector.tsx`**:
  - Added `run.cancel_requested` and `run.cancelled` to `KNOWN_EVENT_TYPES`.
  - Added `badge-warning` styling for cancellation events in `getEventBadgeClass`.
  - Formatted clear summaries for `run.cancel_requested` and `run.cancelled` (showing tokens consumed and latency).
  - Updated `terminalTelemetry` to capture terminal telemetry from `run.cancelled`.
  - Updated `connectionStatus` state machine to include `"cancelling"` and `"cancelled"` terminal states.
- **`App.tsx`**:
  - Added `isCancelling` state and `handleCancelRun` to issue `POST /api/runs/{run_id}/cancel`.
  - Rendered a distinct `⏹ Stop` button in `.form-actions` when `isRunning` is active.
  - Handled `run.cancelled` event in SSE frame processing, appending `[interrupted]` to the streaming answer.
- **`styles.css`**:
  - Added `.btn-cancel` styling with responsive hover and disabled states.
- **Unit Tests**:
  - Added test in `TimelineInspector.test.tsx` verifying `run.cancel_requested` and `run.cancelled` badge rendering and telemetry forwarding.
  - Added test in `App.test.tsx` verifying prompt submission, Stop button rendering, cancel API execution, and interrupted answer rendering.

## Verification
- `npm --prefix web test`: all 23 tests passed across `App.test.tsx` and `TimelineInspector.test.tsx`.
- `npm --prefix web run build`: clean TypeScript check and Vite production bundle build.
- `make test`: all repository test suites (backend, MCP, dataset spike, infra, and frontend) passed.

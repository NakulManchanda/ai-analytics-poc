# 0044 — SSE Stream Close on Cancel & Client-Side Abort

## Goal
Close the SSE event stream immediately upon emitting `run.cancelled` and attach an `AbortController` in the React frontend so clicking Stop unblocks the UI in <50ms without waiting for socket timeouts (fixes #84).

## Starting Point
While `OrchestrationLoop` emitted `run.cancelled` quickly, `services/app/app/routers/events.py` did not treat `run.cancelled` as a terminal event in `event_generator()`. As a result, the SSE HTTP connection remained open, causing the frontend `await sseResponse.text()` to hang until the 30-second server timeout before releasing `isRunning`.

## Decisions
- In `services/app/app/routers/events.py`:
  - Added `"run.cancelled"` to Redis stream and in-memory publisher terminal checks.
  - Added `"cancelled"` to durable state repository terminal reconciliation checks.
  - The SSE HTTP stream now closes cleanly as soon as `run.cancelled` is sent.
- In `web/src/App.tsx`:
  - Attached an `AbortController` to both the run submission and SSE event fetches.
  - Updated `handleCancelRun` to immediately release `isRunning = false` and invoke `abortController.abort()` to terminate in-flight client fetches instantly.

## Verification
- `make test`: All Python services (107 tests) and React frontend tests (23 tests) passed.

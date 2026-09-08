# 0049 — Streaming answer UI: EventSource character-by-character rendering

## Goal

Replace buffered `fetch` response reading with real-time `EventSource` consumption for SSE events during run execution, enabling character-by-character answer rendering in the UI.

## Starting point

The React frontend previously used `fetch(runAccepted.events_url)` and waited for `await sseResponse.text()` to finish before parsing chunks. As a result, users only saw the full answer rendered after the entire stream completed instead of progressive delta rendering.

## Decisions

- Connect directly to `runAccepted.events_url` via `EventSource` in `web/src/App.tsx`.
- Register listeners for named events (`answer.delta`, `run.completed`, `run.failed`, `run.budget_exceeded`, `run.cancelled`, `run.cancel_requested`) and generic `message` events.
- Incrementally append `answer.delta` chunks to `streamingAnswer` state as they arrive in real-time.
- Handle terminal events (`run.completed`, `run.failed`, `run.budget_exceeded`, `run.cancelled`) to extract full telemetry metrics and close the `EventSource` connection.
- Bind `AbortController` cancellation signal to immediately close the `EventSource` and abort cleanly.
- Enhance test mocks in `web/src/App.test.tsx` to faithfully simulate `EventSource` event dispatching.

## Verification and status

- `npm --prefix web test` passed (23/23 tests across `App.test.tsx` and `TimelineInspector.test.tsx`).
- `npm --prefix web run build` compiled client bundle with zero type/lint errors.
- `uv run --project services/app pytest services/app/tests` passed (118/118 tests).

## Lesson

Standard browser `EventSource` dispatches named events (from `event: <name>` SSE lines) to specific event listeners rather than generic `message` handlers. Listening for both specific event types and message events ensures robust interoperability with SSE endpoints.

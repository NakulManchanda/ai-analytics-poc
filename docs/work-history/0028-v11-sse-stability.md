# Work History Entry: 0028

**Goal**: Stabilize the Timeline Inspector SSE connection across same-run parent rerenders while continuing to deliver working-context updates to the current callback.

**Starting Point**: Roadmap issue #44 identified that the Timeline Inspector effect depended on callback identity in addition to the active run ID. Prompt-style parent rerenders therefore cleared the event timeline and recreated the browser EventSource.

---

## Decisions & Changes

1. Kept the EventSource lifecycle effect keyed only to `runId`.
2. Stored `onWorkingContextUpdate` in a React ref that is refreshed on every render, allowing an already-open stream to call the latest parent callback without reconnecting.
3. Added focused EventSource lifecycle tests covering callback-only rerenders, terminal timeline preservation, latest context delivery, and a one-close/one-replacement run-ID transition.

## Verification

1. Red test: `npm test -- TimelineInspector.test.tsx` initially failed as expected because the callback-only rerender created two EventSource instances.
2. Focused frontend test: `npm test -- TimelineInspector.test.tsx` passed with 2 tests.
3. Production build: `npm run build` passed (`tsc --noEmit && vite build`).

## Pull Request and Merge State

- Branch: `codex/v11-sse-stability`
- Tracking issue: #47
- Pull request: [PR #50](https://github.com/NakulManchanda/ai-analytics-poc/pull/50) (draft).
- Merge state: not merged; deployment and infrastructure changes are out of scope.

## Lessons

SSE resource ownership should be tied to its stable identity (`runId`), while changing callback consumers should be represented through a mutable callback reference rather than an effect dependency.

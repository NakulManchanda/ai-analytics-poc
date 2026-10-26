# 0115 — SSE replay keeps run.declined (#186 follow-up)

## Goal
Decline runs must show `run.declined` (with outcome and reason) on the SSE stream even when the run finished before the reader reached its events, and CI must exercise the run streaming path.

## Starting point
`GET /api/runs/{id}/events` reads Redis `run-events` with XREAD from `0-0`, 50 entries per read. On a stream with more than 50 older entries, a batch with no match for this run counted as "nothing found", so the durable reconstruction fallback ran for any already-completed run. Reconstruction had no `run.declined`, so decline runs replayed as `run.received`, `step.llm_proposal`, `step.answer`, `run.completed` and the UI timeline lost that step. Fresh stacks have tiny streams, so earlier live checks and the unit tests (in-memory publisher) never hit it. `make compose-smoke` only called `/api/ask`; `scripts/smoke/09_sse_events.sh` only curls `/api/health` and is not wired to CI.

## Decisions
- The decline answer step now persists `reason` and `tool_source` beside `outcome`, so the decline is durable.
- Reconstruction emits `run.declined` before the answer step when its `outcome` is one of the four non-tool outcomes, with the same payload as the live event.
- A non-empty XREAD batch counts as progress (and skips the poll sleep), so the fallback only runs when the read returns nothing or Redis is unavailable. Non-decline runs take the same code path as before.
- `scripts/smoke/03_compose_ui.sh` gains a run lifecycle section on the fake provider: a tool question, a decline question, and a regression (15 extra runs push the stream past one read, then a decline run is read 3 seconds after completion). The regression case failed on the unfixed code (events were `run.received, step.llm_proposal, step.answer, run.completed`) and passes now.
- Not done: `scripts/smoke/09_sse_events.sh` still only checks `/api/health` despite its name; it is not part of CI, so the new section in 03 covers the path instead.
- Reconstructed `run.failed` still lacks the structured reason (observed, out of scope).

## Verification
- `uv run --project services/app pytest services/app/tests -q`: 463 passed (4 new tests in `test_events.py`; three failed before the fix).
- `make compose-smoke`: failed on the unfixed code at the post-growth decline case; passes with the fix.

## PR / merge state
Branch `fix/186-sse-replay-declined`; PR #205.

## Lessons
- A smoke that never reads the event stream cannot catch replay bugs; the regression needs a stream longer than one read page.
- A fallback keyed on "found nothing" must distinguish an empty read from a read that found other runs' entries.

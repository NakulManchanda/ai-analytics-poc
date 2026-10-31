# 0125 — Phoenix trace links and run id on the review page; Phoenix grid input/output (#190, slice 3)

## Goal
Make it easy to get from a reviewed turn to its Phoenix trace, and to scan runs in Phoenix without opening each one.

## Starting point
The review page showed no run id or trace link; Phoenix's spans grid showed `--` for the `ai.run` Input and Output columns, and finding a run meant guessing a filter (`attributes["ai.run_id"]` matches nothing).

## Decisions
- Review page (`run_review.py`): new **run** column (run id) in the static and interactive page; with `--phoenix-url` (Make targets pass `PHOENIX_URL`) each run links to Phoenix filtered to that run's `ai.run` span. No `trace_id` is needed in captures: Phoenix's span filter is in the URL and `ai.run_id` is already an attribute. The condition uses the nested form `attributes["ai"]["run_id"]`, checked against Phoenix's GraphQL API (the flat form returns nothing). Project id defaults to `Project:1`; `--phoenix-project-id` overrides. Time range is the last 7 days.
- Phoenix grid: `ai.run` gets `input.value` (question, 200 chars) and `output.value` (answer, or `failed: <code>`) only when `AI_TRACE_RUN_IO=1`. Off by default because the existing contract test keeps prompts and answers out of spans for any OTEL backend; `docker-compose.phoenix.yml` (local only) sets it to 1. The shared tracing env moved to an `x-phoenix-tracing` anchor so the app-only flag does not leak into the MCP service.
- Out of scope: capturing `trace_id` in results, annotating Phoenix with verdicts, the baseline review.

## Verification
- `make evals-test`: 158 passed, 2 skipped (run column, link present only with a Phoenix URL, filter decodes to the nested condition and is quoted safely).
- `uv run --project services/app pytest services/app/tests/test_orchestration_tracing.py`: 13 passed (opt-in off by default, bounded input/output, `failed: <code>` on error).
- Phoenix GraphQL: the generated filter returned the `ai.run` span for a real failed run from the 2026-10-30 Nova Pro run. `docker compose config` with the Phoenix overlay shows `AI_TRACE_RUN_IO: "1"` on the app only.
- Not verified: the Input/Output columns in the Phoenix UI (needs the app image rebuilt with this change) and the `timeRangeKey=7d` link opening in a browser.

## PR / merge state
Branch `feat/190-phoenix-links`; draft PR.

## Lessons
- Phoenix nests dotted attribute keys, so filters must index `attributes["ai"]["run_id"]`; testing the filter against the GraphQL API caught a wrong filter I had suggested earlier.

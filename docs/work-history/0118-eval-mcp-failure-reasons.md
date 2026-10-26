# 0118 — Eval compare/review split MCP failures by reason (#209)

## Goal
`run-compare` and `run-review` show why MCP tool calls failed (`row_limit_exceeded`, `transport_error`, ...) instead of one `mcp_tool_error` bucket, as they already do for declined outcomes.

## Starting point
#210 added `tool_error: {"reason", "tool_name"}` to the `run.failed` payload. The eval tooling read only `failure_code` from saved SSE captures (`events/<run_id>.txt`).

## Decisions
- `_frame_field` now sits on a small `_frame_payload` helper so a nested field can be read without a second parser; its behaviour is unchanged.
- `mcp_reason_histogram` counts `tool_error.reason` over captures whose `run.failed` frame has one. Captures without `tool_error` (older runs, non-MCP failures) are skipped rather than counted as `unknown`, so existing output only gains a `none` row.
- One row each in `compare.md` ("MCP failure reasons (this run)") and `review.html` (`#mcp-failure-reasons`), next to the failure-code and declined rows.
- Out of scope: capture-record schema, app code, web, byte-cap truncation.

## Verification
- Tests written first failed on the old code (`ImportError` for `mcp_reason_histogram`).
- `uv run --project tooling/evals pytest tooling/evals/tests -q`, `black --check tooling/evals`, `ruff check tooling/evals`: 125 passed, 2 skipped; black and ruff clean.

## PR / merge state
Branch `feat/209-eval-mcp-reasons`; PR #212.

## Lessons
- Keeping "absent" distinct from "unknown" avoids noise when a new payload field only exists on newer captures.

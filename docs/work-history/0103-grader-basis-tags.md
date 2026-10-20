# 0103 — Grader basis tags and separate grading outcomes (#188)

## Goal
Make golden cases declare which filter basis their reference depends on, mark diagnostic and fault-injection cases, and report execution, tool and answer outcomes separately with `needs_review` never counted as pass or fail.

## Starting point
The first live baseline showed the legacy graders passing wrong answers and failing acceptable ones, partly because legacy (unfiltered) and governed (valid-records) tools legitimately give different numbers (see 0102). Tool results were not available to graders because `capture.v1` does not carry them.

## Decisions
- Slice B1: tags only in `cases.jsonl` (`basis-dependent` + one `basis:*`, `diagnostic` on `multi-001` and `fail-003`, `fault-injection` on `fail-001`, `fail-002`, `fail-004`); existing tags kept, only `tags`/`notes` edited, no reference, facts, tools, question, review or version changes.
- A dataset test compares against the `main` copy and asserts only tags/notes differ.
- Tool output comes from an optional events directory (`load_tool_outputs` from `run_<id>.txt` SSE events), joined to `TurnView.tools` via `attach_outputs`. Without it, output-dependent grounding checks degrade to `needs_review`.
- Grader v2 (`grade_turn_v2`) returns independent `execution`, `tool`, and `answer` outcomes (`correct`, `incorrect`, `needs_review`, `not_applicable`) along with detailed human-readable `reasons`.
- `session.py` enhanced with `--events-dir` argument, reporting three outcome rates, diagnostic-separated counts, fault-injection exclusions, and tool basis breakdowns.
- Post-review hardening:
  - Unexpected extra tool calls rejected in `_grade_tools`.
  - SSE event parser requires `role == "tool"`, validates run identity, and discards conflicting duplicate query IDs across full grading payload (rows, columns, truncated, row_count).
  - Entity-to-metric attribution hardened using closest metric keyword to number span; returns `needs_review` when multi-entity multi-metric cases lack confident attribution.
  - Tool equivalence strictly governed by `_is_tool_proven_equivalent`: only permitted for established equivalent tool pairs matching dimensions, measures, and filters; numbers matching in output never infer equivalence on their own.
  - Empty runs with no model calls or tools explicitly fail execution; execution represents observed activity without recorded failure (steps <= budget, no model/tool errors, non-empty activity).

## Verification
- Unit test suite: `uv run --project tooling/evals pytest tooling/evals/tests` -> 88 passed, 2 skipped in 0.21s.
- Linter: `uv run --project tooling/evals ruff check tooling/evals` -> all clean.
- Dataset validation: `uv run --project tooling/evals evals validate` -> `ok: 37 cases`.
- Dataset integrity check: all facts and reference values verified 100% byte-identical to `main`.
- Regrade: evaluated frozen snapshot `864e0cdebd...` without calling Bedrock:
  - Gated execution: 37/37 correct (100%).
  - Gated tool: 13 correct, 23 incorrect, 1 needs_review.
  - Gated answer: 10 correct, 22 incorrect, 5 needs_review.
  - Diagnostic: 3 turns (1 correct, 1 incorrect, 1 needs_review).
  - Fault injection: 3 turns (3 not_applicable).
  - False positives eliminated: `det-006` (truncation), `det-007` (missing facts), `det-009` (false comparative), `det-010` (label mismatch), `det-013` (fabricated numbers) now correctly fail.
  - False negatives eliminated: `multi-004#0` correctly passes answer with `needs_review` tool equivalence; `uns-004` cleanly separates incorrect tool call from correct answer decline.

## PR / merge state
- PR NakulManchanda/ai-analytics-poc#192.
- Branch `feat/188-grader-basis-tags`.

## Lessons
- Separating execution, tool correctness, and answer correctness prevents false conflations (e.g. an unneeded tool call on an unsupported query should not fail an otherwise correct decline answer).
- Reading tool result rows from SSE events bridges the capture schema gap without requiring breaking changes to the core application schema.

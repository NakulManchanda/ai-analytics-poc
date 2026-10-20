# 0103 — Grader basis tags and separate grading outcomes (#188)

## Goal
Make golden cases declare which filter basis their reference depends on, mark diagnostic and fault-injection cases, and report execution, tool and answer outcomes separately with `needs_review` never counted as pass or fail.

## Starting point
The first live baseline showed the legacy graders passing wrong answers and failing acceptable ones, partly because legacy (unfiltered) and governed (valid-records) tools legitimately give different numbers (see 0102). Tool results were not available to graders because `capture.v1` does not carry them.

## Decisions
- Slice B1: tags only in `cases.jsonl` (`basis-dependent` + one `basis:*`, `diagnostic` on `multi-001` and `fail-003`, `fault-injection` on `fail-001`, `fail-002`, `fail-004`); existing tags kept, only `tags`/`notes` edited, no reference, facts, tools, question, review or version changes.
- A dataset test compares against the `main` copy and asserts only tags/notes differ.
- Tool output comes from an optional events directory (capture gap documented in `docs/evals.md`).
- TODO: slice A (traces/graders/schema) and slice B2 (checks, CLI flag, report sections) decisions.

## Verification
- TODO: `uv run python -m evals.cli validate` and `uv run pytest -q` results, dataset sha256, full suite, ruff, diff and secret scan.

## PR / merge state
TODO

## Lessons
TODO

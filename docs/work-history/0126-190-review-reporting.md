# 0126 — Human review in eval reporting; baseline adjudication workflow (#190, slice 4)

## Goal
Finish the reporting side of #190: show human verdicts next to grader outcomes (never merged) and let a reviewer adjudicate the frozen 2026-10-20 baseline end to end.

## Starting point
`reviews.resolved.json` existed (#217) but `run-compare` and the reports ignored it; nothing summarised review progress.

## Decisions
- `evals/review_report.py`: `summary_lines` (coverage, verdict counts per dimension, grader-vs-human agreement and disagreement table with failure layer/category/notes, counts of records not applied) and `queue` (what is left, ordered: run did not complete, grader `needs_review`, grader `incorrect`, then spot checks). `evals run-review-report` / `make eval-review-report` write `<run>/review-report.md`.
- `run-compare` appends the same human section when `reviews.jsonl` exists; the grader tables are untouched, so grader and human outcomes stay separate.
- `reviews.coverage()` extracted from `materialize` so the export and the report share one definition of complete/partial/untouched.
- Baseline adjudication is documented in `docs/evals.md` (graded JSON prerequisite, review, report, export, and that golden/grader fixes go through a PR). The verdicts themselves are human work and are not part of this PR.
- `make next-5-commands` now ends with the report step and lists the no-Docker baseline adjudication commands.
- Out of scope: failure layer/category inputs on the review page (records already carry them), cross-run human-vs-human comparison, Phoenix annotations.

## Verification
- `make evals-test`: 164 passed, 2 skipped (`tests/test_review_report.py`: no-reviews report and compare, disagreement table with escaped notes, partial coverage, compare section once, queue order, CLI file).
- Ran `run-review-report` on a scratch copy of the real baseline with three saved verdicts: coverage 1 complete / 1 partial / 41 untouched, one disagreement listed, and 7 not-completed runs first in the queue.
- black and ruff clean.

## PR / merge state
Branch `feat/190-review-reporting`; PR #220.

## Lessons
- zsh does not word-split unquoted variables; use a shell function when scripting repeated CLI calls.
- A scripted doc edit that fails on its anchor must stop the commit: check the script's exit status before committing.

# 0123 — Human review data layer, no UI (#190, slice 1)

## Goal
Durable, auditable human verdicts for frozen eval runs, before any review UI exists.

## Starting point
`review.html` (#200) was read-only; frozen runs had `captures.snapshot.sha256` but nothing recorded human judgement.

## Decisions
- `evals/reviews.py`: append-only `<run>/reviews.jsonl`, schema v1. Dimensions `tool|answer|resolution`; verdicts `correct|incorrect|needs_review`; optional failure layer/category and notes; reviewer and tz-aware `reviewed_at` required.
- Binding: each record carries `eval_run_id` and the snapshot digest. The digest is re-verified against the snapshot file; a modified or missing snapshot blocks both writing and reading. Records bound to another run or digest are reported as `stale` and never applied.
- Resolution per (case, turn, dimension): latest `reviewed_at` wins, file order breaks ties; earlier review ids are kept under `superseded`. A repeated `review_id` is ignored (first wins). Malformed lines are reported with line numbers and never fatal.
- Grader results stay in `report/*.json`; `disagreements` compares them to human verdicts on tool/answer only.
- `evals run-review-add` and `run-review-export` (writes `reviews.resolved.json`, read back by `load_human_verdicts`). Golden cases, captures and reports are not modified.
- Out of scope (later slices): review UI and save mechanism, Phoenix deep links, baseline review and `docs/evals.md` workflow.

## Verification
- `make evals-test`: 137 passed, 2 skipped; `tests/test_reviews.py` covers supersession, timestamp ordering and ties, duplicates, malformed rows, stale evidence, tampered snapshot, disagreements, and the export/import round trip.

## PR / merge state
Branch `feat/190-review-data-layer`; draft PR.

## Lessons
- The existing freeze digest made evidence binding almost free; the new work was the resolution rules.

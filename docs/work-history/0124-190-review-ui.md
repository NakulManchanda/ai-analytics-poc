# 0124 — Review page that records human verdicts (#190, slice 2)

## Goal
Let a reviewer record verdicts per turn without editing JSONL, on top of the data layer from #217.

## Starting point
`reviews.jsonl` could be written only through `evals run-review-add`; `review.html` was static and read-only.

## Decisions
- UI choice: extend `run_review.render_review` with an `interactive` flag (extra "human review" column, unreviewed and disagreement filters) rather than add Streamlit. A static file cannot write, so a small stdlib helper (`review_server.py`, `evals run-review-serve`, `make eval-review-serve`) serves the page and appends verdicts through the existing `append_review`. No new dependency or service.
- The helper binds `127.0.0.1`, accepts only `Host` / `Origin` of itself (blocks cross-site posts and DNS rebinding), caps the body at 64 KiB, validates a whole batch before writing any of it, and takes the reviewer name from the command line.
- The static `review.html` output is unchanged (tested).
- Out of scope: Phoenix deep links and span mirroring, the baseline review itself.

## Verification
- `make evals-test`: 141 passed, 2 skipped (new `tests/test_review_server.py`: form present, save then disagreement shown, bad batch writes nothing, foreign host/origin rejected).
- Live smoke on a throwaway run: served the page, POSTed one verdict with curl, reloaded and saw the grader disagreement.

## PR / merge state
Branch `feat/190-review-ui-form`, stacked on `feat/190-review-data-layer` (#217).

## Lessons
- A shared fixture belongs in `conftest.py`; importing it from another test module trips lint.

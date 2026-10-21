# 0111 — standard eval run folder (#200)

## Goal
Make a live golden eval run repeatable and hard to mis-run: one folder per run, a label that cannot be reused, a tracked driver, logged make targets, a combined command and a static `review.html`.

## Starting point
Baseline 2 ran from untracked scratch scripts: files were spread across two folders, smoke and full-run captures shared one directory, `session-report` needed a per-conversation loop, command output was not kept, and nothing stopped a label from being reused.

## Decisions
- Layout mirrors `metrics/inference/<RUN_ID>`: `metrics/evals/<run_id>/`, gitignored. `run_id = <label>-<UTC ts>`; `run-start` refuses a label that already has a run (exact match on `<label>` or `<label>-<ts>`).
- The app's capture mount is fixed at stack start (`metrics/evals/_capture`), so a run snapshot is filtered out of it by exact `eval_run_id`; the smoke run uses `<run_id>-smoke` and writes under `<run>/smoke/`.
- Python parts live in `tooling/evals` (stdlib + existing session-report code reused in-process); the make targets only call them. `scripts/eval-log.sh` tees every target into the run's `logs/` and keeps the exit status.
- Real Bedrock targets (`eval-smoke`, `eval-run`, `eval-all`) need `EVAL_CONFIRM=1`.
- `review.html` is a static read-only report of our own grades; it is not a trace viewer, and it does not record verdicts (#190 owns that; Langfuse/Phoenix remain optional later backends).

## Verification
- `tooling/evals` suite: 101 passed, 2 skipped; black and ruff clean.
- `run-compare`/`run-review` on the real Baseline 2 run reproduce the hand-run numbers; `review.html` has no external URLs.
- Make flow on a throwaway root: start, refused reuse, spend guard, failing freeze keeps its exit status, logs land in the run folder. This caught a real bug (targets did not pass `--root`), fixed before the PR.
- Live, from an isolated compose project of the worktree (fresh LiteLLM DB): smoke, two full end-to-end runs (43/43 recorded, 96 capture lines kept, 37 reports, compare, cost, `review.html`); the same 7 `run.failed` turns as the manual Baseline 2, graded as valid evidence; counts match Baseline 2 (tool correct 20, answer correct 12); LiteLLM $0.005428 vs capture $0.005360.
- Not demonstrated live: Ctrl-C interruption and `--resume` (covered by tests only; the live attempt completed before it could be interrupted and exposed a `--resume` default bug, fixed).

## PR / merge state
PR #201 (reviewed three times; findings: incomplete-evidence exit, checkpoints/resume, SSE frames, structured reports, completeness over the full manifest, submit journal + allowlist, `lateral`, multi-turn continuation after a failed turn documented and marked).

## Lessons
- `make -n` still executes recipe lines that contain `$(MAKE)`; keep such lines free of side effects.
- Testing a make wrapper with a non-default root exposed an argument that was silently ignored; always run the override path once.

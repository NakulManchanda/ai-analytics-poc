# 0138 — Opt-in Temporal eval workflow pilot and crash-recovery proof (#253, parent #251)

## Goal
Evaluate whether Temporal adds meaningful durability over the existing file-checkpointed `evals run-live --resume` CLI on the offline golden evaluation workload, while keeping Temporal strictly opt-in and local under `tooling/evals/`, leaving `services/app`, `services/mcp`, `web/`, and base `docker-compose.yml` untouched, proving real OS-process `SIGKILL` crash recovery, and delivering an evidence-backed **keep / stop** recommendation.

## Starting point
`tooling/evals/src/evals/live_run.py` executed golden cases sequentially in a single local CLI process with file checkpointing (`results.jsonl`, `matches.json`, `submitted.jsonl`, `state.json`). On a mid-case crash, `evals run-live --resume` moved incomplete case turns to `results.superseded.jsonl` and re-ran the whole multi-turn case in a fresh conversation, leaving prior accepted runs as billed orphans. `POST /api/runs` did not expose a client-supplied submission-key lookup endpoint.

## Decisions
- **Isolated optional dependency (`tooling/evals/pyproject.toml`, `uv.lock`):** Added `temporalio>=1.20,<2` (`1.36.1`) under `[project.optional-dependencies] temporal` and `[dependency-groups] dev` so `make evals-test` and CI always run the crash-recovery proof without polluting non-dev runtime installs or requiring Docker. Default `evals` commands lazy-import `temporal_pilot` and never import `temporalio`.
- **Crash-safe submission journal (`tooling/evals/src/evals/temporal_store.py`):** Implemented deterministic `submission_key = f"{eval_run_id}:{case_id}:t{turn_index}"`, `fcntl.flock`-protected writes to `<run_dir>/submissions.jsonl` plus standard run-folder artifacts (`submitted.jsonl`, `results.jsonl`, `matches.json`, `events/<run_id>.txt`, `state.json`), non-blocking per-case lock `<run_dir>/.locks/<case_id>.lock` (`CaseInFlightError`) preventing overlapping attempts, tolerant JSONL tail reading for mid-append `SIGKILL` recovery, definite HTTP 4xx `submit_error` vs network-timeout/5xx `ambiguous_submission` classification, optional `x-eval-submission-key` request header, and optional `GET /api/runs/by-submission/{key}` lookup that degrades cleanly on `404`/`405`/`501`.
- **Deterministic workflow + separate worker (`tooling/evals/src/evals/temporal_pilot.py`, `runs_cli.py`, `cli.py`):** Implemented `EvalBatchWorkflow` (zero direct network/filesystem I/O, whole multi-turn case as the Activity unit, per-case `start_to_close_timeout` sized from turn count, `non_retryable_error_types=["ValueError", "RunError"]`, `init_eval_activity` so client attach never overwrites `state.json`, heartbeated `execute_case_activity` wrapped in `try/except ActivityError`, and guaranteed `finalize_eval_activity` reusing `run_freeze`/`run_grade`/`run_review_cmd`), `evals temporal-worker`, and `evals run-temporal` with deterministic `workflow_id = f"eval-{run_id}"`.
- **No production test hooks for crash proofs:** Simulated post-accept and blind-window crashes in `tooling/evals/tests/test_temporal_pilot.py` purely by holding the fixture HTTP server's SSE or POST `202` response while killing a real child `evals temporal-worker` OS process via `os.kill(pid, signal.SIGKILL)`.
- **Opt-in local Compose + guarded Make targets (`docker-compose.temporal.yml`, `Makefile`):** Added a standalone local Temporal dev server (`temporalio/temporal:1.10.1`, loopback-bound `127.0.0.1:7233` / `8233`) and `eval-temporal-up`, `eval-temporal-down`, `eval-temporal-worker`, and `eval-temporal-run` (gated on `EVAL_CONFIRM=1`).
- **Keep / stop decision (`docs/evaluation/temporal-eval-pilot.md`):** Keep strictly as an opt-in pilot and stop before default adoption. Post-accept SSE reconciliation is a real win over `--resume` superseding, but blind-window deduplication still requires an app-side submission key, and a standalone Temporal server + worker adds operational overhead unjustified at the current 37-case / 43-turn local POC scale.

## Verification
- Step 1 probe (`WorkflowEnvironment.start_local()` + child worker `SIGKILL` recovery): passed cleanly in 18.32s (`start_local` ready in 4.96s).
- `uv run --project tooling/evals pytest tooling/evals/tests`: **216 passed, 2 skipped** (including all 11 new Temporal unit, negative, and real OS-process `SIGKILL` crash-recovery tests across Scenarios 1–3, POST timeout/4xx classification, per-case locking, truncated JSONL tail handling, and guaranteed workflow finalization).
- `uv run --project tooling/evals black --check tooling/evals` (44 files unchanged), `uv run --project tooling/evals ruff check tooling/evals` (all checks passed), and `uv run --project tooling/evals python -m compileall -q tooling/evals/src`: clean.
- `docker compose -f docker-compose.temporal.yml config >/dev/null`: validated cleanly; zero developer-specific absolute paths or secrets in changed files.
- No live paid Bedrock/LiteLLM traffic was executed; the tiny 2-case live command is documented and gated on `EVAL_CONFIRM=1`.

## Limitations / lessons
- Without a server-side `GET /api/runs/by-submission/{key}` endpoint in `services/app`, a `SIGKILL` inside the narrow window between `POST /api/runs` transmission and `202` receipt cannot be auto-reconciled; the journal safely records `ambiguous_submission` and refuses blind retry to avoid duplicate LLM spend.
- Much of the post-accept reconciliation value lives in `temporal_store.py` (journal + SSE replay) rather than in Temporal itself, which is a useful finding for deciding whether to fold post-accept reconciliation into `run-live --resume` later.

## PR / merge state
Draft PR on branch `feat/253-temporal-eval-pilot` (worktree `.worktrees/253-temporal-eval-pilot`), referencing `Closes #253` and `Parent: #251`.

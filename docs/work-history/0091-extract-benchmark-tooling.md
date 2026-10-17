# Work 0091 — Extract benchmark tooling

## Milestone

Repository structural refactor, epic #160, issue #161 (1 of 3)

## Branch

`refactor/161-benchmark-tooling`

## Worktree

`.worktrees/161-benchmark-tooling`

## Goal

Move the offline replayer, scenario models/loader, goodput, metrics scraper, parity, trace and E5 generator out of the production app package into `tooling/benchmarks/`, with no behavior change.

## Starting state

These modules lived in `services/app/app/benchmarks/` and `services/app/app/scenarios/`; the CLIs lived in `services/app/scripts/`. The app image therefore shipped test-only code.

## Decisions

- `tooling/benchmarks` is its own uv project (httpx, pydantic). Fixtures remain in `config/scenarios/`.
- Two checks depend on app internals (`app.query_catalogue.lookup`, `app.prefix.build_query_proposal_partition`). They are isolated in `benchmarks/app_checks.py` with lazy imports and are available through the optional `app` extra (path dependency on `services/app`). Core replay does not import the app. `canonical_prefix.py` was folded into `app_checks.py`.
- `DEFAULT_SCENARIOS_DIR` re-anchored to the repo root; tests no longer rely on the working directory.
- Make target names and flags unchanged; only the command prefix changed (`python -m benchmarks.run`). `make test` now includes `bench-test`.
- `tooling/evals/` is reserved only; DeepEval is out of scope.

## Verification

- Before and after stub-server replays (`shared_prefix_fanout` as `app_runs`, `e5_recompute_control_2k` as `gateway_chat`) produced identical manifests, scenario/execution hashes, `system_prefix` and request prompts (only timestamps differ).
- Tests: 353 before (220 app + 133 moved), 353 after; `tests/` 93 passed, 1 skipped. black and ruff are clean.

## Limitations

- CI changes verified by reading only until the PR's Actions run.
- The serve smoke (`inference-serve-smoke`) needs a live gateway and was not run; its imports resolve.
- Without the `app` extra, 4 tests that need the app fail; `bench-test` always uses the extra.
- Historical work-history entries and the ADR still mention old paths by design.

## Lessons

Isolating the app-coupled checks first made the move purely mechanical.

## Pull request

Draft PR #164 (closes #161); awaiting review.

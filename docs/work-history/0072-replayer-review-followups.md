# Work history 0072 — Replayer and scenario loader review follow-ups (#142 review)

## Goal

Address the two Copilot review findings on PR #142 (merged) for the scenario generator/replayer.

## Starting point

- `DEFAULT_SCENARIOS_DIR` was CWD-relative, so `load_scenario()` failed outside the repo root.
- After a failed `/api/runs` turn, the replayer set `conv_id` to its synthetic label and sent it on later turns, producing misleading 404s.

## Decisions

- Anchor `DEFAULT_SCENARIOS_DIR` to the repo root via `Path(__file__)`.
- Keep the previous `conv_id` (empty for `/api/runs`) when a turn returns no server conversation id.
- Regression test: `test_replayer_never_sends_synthetic_id_after_first_turn_failure`.

## Verification

- `uv run --project services/app pytest services/app/tests/test_replayer.py services/app/tests/test_scenarios.py services/app/tests/test_run_scenario_cli.py` — 12 passed, plus the new test.

## PR / merge state

Open PR (this change). The fix was first pushed directly to `main` by mistake (`47b7ee6`), reverted (`b26d717`), and re-landed through this PR.

## Lessons

- Even small fixes touching a test plus source files beyond the 1–2 file exception should use the PR workflow.

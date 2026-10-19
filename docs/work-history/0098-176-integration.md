# 0098 — #176 capture + model policy + evals integration

## Goal
Make the three merged tracks work together: per-run policy bound in the loop, capture that
reports provider facts honestly, real `capture.v1` ingest in `tooling/evals/`, and a report for one
captured multi-turn taxi session.

## Starting point
main with #173 (capture), #174 (model policy) and #175 (evals) merged. Policy budgets and
entitlements were inert because nothing bound `call_context`; capture treated 0 tokens as unknown;
evals could only load synthetic traces carrying `case_id`.

## Decisions
- `execute()` is the single choke point, so the binding lives there (tenant is still `default`).
- `LLMCall` carries the provider-result policy fields; capture uses `usage_known`, not "0 means unknown".
  `run_started_at` and `conversation_id` were added to capture records so exported sessions can be re-ordered.
- Evals matching is explicit and human-confirmed; suggestions never label. Pending-review golden cases
  are not graded unless `--provisional`, and the report says so. `build_turns()` skips unlinked records.
- The session fixture is recorded through the real loop/policy/capture with a scripted provider; it is
  labelled NOT LIVE everywhere. Live Bedrock was not available.

## Verification
`uv run --project services/app pytest services/app/tests`, `make evals-test`, black and ruff clean (see PR for counts).
Budget exhaustion is tested through a real loop run (`run_budget_exhausted`, answer call never sent).

## PR / merge state
See the PR.

## Lessons
A single catalogue question matches several golden turns (`det-004` and `multi-001#0`), so exact-text
suggestion cannot be trusted as a label; the human match file is what makes the link.
The catalogue path skips the planner model call, so some turns legitimately have no planner record.

# 0101 — Fix golden case multi-001 reference

## Goal
Make the `multi-001` reference consistent with the governed tool's default filters before the first live Bedrock run.

## Starting point
Turn 1's reference SQL had no date or quality filters and expected `JFK Airport` as the top pickup zone. Turn 2's SQL applied the filters, so the two turns contradicted each other. A correct agent would have failed turn 1.

## Decisions
- Apply the same filters to turn 1 and recompute with `evals refresh-reference`: top zone is `Midtown Center` (141,740 trips); turn 2 (`Upper East Side South`, 141,266) is unchanged. Dropped an unused `dz` join.
- Regenerated synthetic fixtures (`evals synth`) and the scripted captured session, `matches.json` run ids and the provisional report from the corrected reference.
- `review.status` stays `pending_human_review`; this is not a human sign-off.

## Verification
`make evals-test` (47 passed, 2 skipped); `uv run --project services/app pytest services/app/tests` (273 passed).

## Lessons
Reference SQL for multi-turn cases must share one filter basis; cross-check each turn against the governed tool defaults.

# 0102 — Verify golden cases against the real governed MCP tools

## Goal
Check every golden turn with expected tools against the live MCP tools and correct references that assumed a filter basis the tool does not use.

## Starting point
#182 changed `multi-001` turn 1 to the governed (filtered) basis, but `query_taxi_data(top_pickup_zones)` is unfiltered and returns JFK Airport first (145,240). Other reference SQL had never been compared to tool output.

## Decisions
- Called each expected tool (no LLM) in the running stack and compared with the stored facts; all but `multi-001` and `det-009` already matched.
- `multi-001`: turn 1 reverted to the unfiltered result (JFK Airport 145,240); turn 2 expects Midtown Center as the second zone; the trip-count fact is dropped because the compare tool's filtered basis contradicts turn 1. Flagged for a human.
- `det-009`: SQL now mirrors `average_trip_metrics` (`trip_distance >= 0`, `fare_amount >= 0`, no month filter): Manhattan 2.67 / 15.05.
- Notes added on `det-004`, `det-005`, `fail-003` (basis or spelling sensitivity). All cases stay `pending_human_review`.
- Regenerated synthetic fixtures, the scripted captured session, `matches.json` run ids and the provisional report.

## Verification
`evals refresh-reference` (60 facts), `make evals-test` (47 passed, 2 skipped), `uv run --project services/app pytest services/app/tests` (273 passed).

## Lessons
Reference SQL must be checked per tool, not per case: legacy and governed tools use different record filters. Open question for a human: unify the bases.

## Carried-over limitations from #181 (documented in `docs/validation.md`)
- Validation retry reserves about 2x the previous proposal's tokens: an estimate, not a guarantee; runtime budgets enforce actual use.
- Numeric grounding is syntactic and does not prove the number belongs to the right metric or region.
- Fixtures: the captured session was regenerated after #183 so it carries prompt attribution.

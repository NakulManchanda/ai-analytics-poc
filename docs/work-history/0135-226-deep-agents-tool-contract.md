# 0135 — Deep Agents tool description for empty `dimensions` (#226, parent #224)

## Goal
Step 1 of the Deep Agents improvement list: stop the model from sending `dimensions: []` to `aggregate_taxi_data`.

## Starting point
v5 (`deep-agents-defaults-v5-20261108T043701Z`): 6 of 6 tool failures were that call, in 3 runs; one run hit the tool-call cap. `minItems: 1` was already in the schema.

## Decisions
- Keep the "at least one dimension" contract (the golden set expects it); fix the model-facing description instead.
- Deep Agents only: the hint lives in `deep_agents_strategy.py`, not in the MCP server, so the manual path is unchanged.
- No live eval run in this PR; the measurement command is in `docs/deep-agents/01-tool-contract.md`.

## Verification
Focused Deep Agents tests 56 passed, 1 skipped; black and ruff clean. Behavioural effect unmeasured.

## PR / merge state
Draft PR from `feat/226-deep-agents-tool-description`.

## Lessons
A schema constraint (`minItems`) is not enough for this model; the description has to say how to express the intended case.

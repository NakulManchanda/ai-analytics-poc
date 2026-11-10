# Deep Agents improvement 1 — tool contract (empty `dimensions`)

Part of the five-step improvement list for the Deep Agents strategy (#226, epic #224). Each step is its own PR; later steps wait for an explicit go-ahead.

## Problem
In the v5 eval run (43 golden turns, default budgets), every one of the 6 `tool.failed` events was the same: `aggregate_taxi_data` called with `dimensions: []`, rejected as `invalid_arguments`. They occurred in 3 runs. In `multi-004#0` ("average fare for Manhattan pickups") the model recovered on the next call by grouping on the filter column, which cost an extra call and marked the turn tool/execution incorrect. In `react-005` the model repeated the `[]` call four times and hit the 5-tool-call cap.

## Cause
- The contract is intentional: the parser (`_parse_aggregate_taxi_data`) requires at least one dimension, and the golden set expects `dimensions: ["pickup_borough"]` for a filtered single total.
- The Deep Agents tool schema already carries `minItems: 1` (checked in the running app container), but the model ignores it.
- The published description is one sentence and does not say how to ask for a single total, so `[]` looks like the natural encoding.

## Change
`services/app/app/orchestration/deep_agents_strategy.py` appends one short hint to the `aggregate_taxi_data` description (Deep Agents only; the MCP server, the parser and the manual path are untouched). It states that `[]` is rejected and that a single total is requested by grouping on the filtered dimension.

## Deliberately not done
Allowing an empty `dimensions` (a single total with no grouping) would change the MCP contract, the golden expectations and the manual path (#207). That is a product decision and is left open.

## Verification
- `uv run pytest tests/test_deep_agents_strategy.py tests/test_deep_agents_run.py`: 56 passed, 1 skipped (live LiteLLM check); black and ruff clean.
- Effect on behaviour is **not measured yet**. To measure: restart the stack with `AGENT_STRATEGY=deep_agents`, then `make eval-start LABEL=deep-agents-desc-v1 && make eval-health && make eval-run EVAL_CONFIRM=1` and the freeze/grade/compare steps; success is zero `invalid_arguments` events and fewer extra `aggregate_taxi_data` calls than v5 (6 failures, 61 tool calls). About $0.35.

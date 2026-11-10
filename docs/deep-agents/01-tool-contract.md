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

## Bigger picture: what the empty-`dimensions` fix does not cover
Breakdown of the v5 graded rows (43 golden turns, grader v3), counted from `report/conv_*.json`:

| Gap vs manual | Count in v5 | Caused by empty `dimensions` | Other causes |
|---|---|---|---|
| Execution incorrect | 9 | 3 | 6 step-budget breaches (the grader allows 1 step; the model took 2 or 3, 3 turns each) |
| Tool incorrect | 26 | 3 | about 14 "expected tool not matched" (wrong tool or arguments vs the golden call), about 6 "unneeded or extra call", the rest mixed |

So this fix recovers at most about 3 execution and 3 tool turns. It is a cheap, safe fix, not the main lever.

### Not yet addressed by any of the five steps
- **Tool-mismatch turns (about 14).** Open question: are these wrong model choices, or acceptable alternative calls that the golden `expected_tools` do not allow (for example `match: subset` too strict)? Needs a turn-by-turn read before any fix; it is the most likely accuracy lever.
- **Step-budget breaches (6).** Deep Agents takes 2 or 3 steps where the golden turn allows 1. Related to loop overhead (86 model calls vs manual's 65); could also be a grader budget that does not fit this loop style.

### Effect on the plan
Step 2 (per-call overhead: 8.6k vs 2.9k input tokens per turn) is the likely cost lever. The tool-mismatch read-through above should be inserted as a research step before more fixes are chosen.

# Deep Agents improvement 1 — tool contract (empty `dimensions`)

Part of the five-step improvement list for the Deep Agents strategy (#226, epic #224). Each step is its own PR; later steps wait for an explicit go-ahead.

## Problem
In the v5 eval run (43 golden turns, default budgets), every one of the 6 `tool.failed` events was the same: `aggregate_taxi_data` called with `dimensions: []`, rejected as `invalid_arguments`. They occurred in 3 runs. In `multi-004#0` ("average fare for Manhattan pickups") the model recovered on the next call by grouping on the filter column, which cost an extra call and marked the turn tool/execution incorrect. In `react-005` the model repeated the `[]` call four times and hit the 5-tool-call cap.

## Cause
- The contract is intentional: the parser (`_parse_aggregate_taxi_data`) requires at least one dimension, and the golden set expects `dimensions: ["pickup_borough"]` for a filtered single total.
- The Deep Agents tool schema already carries `minItems: 1` (checked in the running app container), but the model ignores it.
- The published description is one sentence and does not say how to ask for a single total, so `[]` looks like the natural encoding.

## Change (revised: the contract changed, not just the description)
The first commit on this branch only appended a hint to the description. After review the contract itself was changed so the natural call works:
- `dataset_spike/query_spec.py::validate_dimensions`: an empty list is valid and means one overall total. The compiler already handled it (no `GROUP BY`, output columns = measures only); test added.
- `app/validation/proposal.py::_check_name_list(allow_empty=True)` for `dimensions` (the manual path accepts it too), `loop.py::_parse_aggregate_taxi_data` accepts `[]` (the field is still required), and the Deep Agents schema no longer forces `minItems: 1` on `dimensions`.
- The description hint now says: "dimensions [] returns one overall total; add names to group."
- Grader v4: an expected dimension that the call omits still matches when an equality filter on that same name pins it (`dimensions: []` with `filters.pickup_borough = Manhattan` is the golden `["pickup_borough"]` call). Without this the golden set would grade the right call as incorrect. The compare header says `provisional grader v4`; older runs must be regraded before comparison.
- Guard: if a Deep Agents final answer contains `<untrusted_tool_data` and no tool succeeded in the run, the answer is replaced by a fixed "no verified figures" message and a failed `grounding_check` step is recorded (the fabricated-block defect on `conv_2da5763fad494a8f` turn 3).

## Verification
- `services/app` 636 passed, 1 skipped (live LiteLLM); `services/mcp` 57 passed; `services/dataset_spike` 57 passed; `tooling/evals` 204 passed, 2 skipped; black and ruff clean.
- Behaviour is **not measured live yet**. Success means zero `invalid_arguments`, fewer `aggregate_taxi_data` calls than v5 (6 failures, 61 tool calls), and no new step-budget breaches.
- To measure it live (from this worktree; it rebuilds the local stack, about $0.35 and 5 minutes). Labels cannot be reused, so use a new `-vN` for reruns:
  ```
  AGENT_STRATEGY=deep_agents make eval-stack-up
  make eval-start LABEL=deep-agents-empty-dims-v1 && make eval-health && make eval-run EVAL_CONFIRM=1
  make eval-freeze && make eval-grade && make eval-compare EVAL_BASELINE=metrics/evals/baselines/golden-2026-10-20.jsonl && make eval-reconcile && make eval-trace-check
  ```
- Not changed, and why: replacing the custom allowlist with `excluded_tools` (works, measured, low gain); switching model, a `create_agent` baseline and Bedrock prompt caching (each needs a decision or provisioning).

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

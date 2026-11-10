# 0135 — Deep Agents tool description for empty `dimensions` (#226, parent #224)

## Goal
Step 1 of the Deep Agents improvement list: stop the model from sending `dimensions: []` to `aggregate_taxi_data`.

## Starting point
v5 (`deep-agents-defaults-v5-20261108T043701Z`): 6 of 6 tool failures were that call, in 3 runs; one run hit the tool-call cap. `minItems: 1` was already in the schema.

## Decisions
- First commit only added a description hint; after review the contract changed: empty `dimensions` means one overall total (MCP validator, app validator, Deep Agents parser and schema). The manual path accepts it too.
- Grader v4: an expected dimension pinned by an equality filter may be omitted.
- Guard against fabricated `untrusted_tool_data` answers when no tool ran.
- After the first measured run: server validation messages reach the model, omitted `dimensions` means `[]`, `compare_taxi_segments` schema deduplicated, tool descriptions and the system prompt got short guidance, `AGENT_HARNESS=create_agent` experiment switch, and Bedrock prompt caching in the LiteLLM config (proven with live calls; it also applies to the manual path). `eval-compare` no longer crashes on run-level report files.
- No live eval run in this PR; the measurement command is in `docs/deep-agents/01-tool-contract.md`.

## Verification
Suites: app 636, mcp 57, dataset_spike 57, evals 204 passed; black and ruff clean. Behavioural effect unmeasured; the live run is next.

## PR / merge state
Draft PR from `feat/226-deep-agents-tool-description`.

## Lessons
A schema constraint (`minItems`) is not enough for this model; the description has to say how to express the intended case.

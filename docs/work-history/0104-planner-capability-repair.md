# 0104 — Planner capability and catalogue repair (#186)

## Goal
Expose all six authorized governed MCP tools to planner model(s) with valid schemas, allow clean typed non-tool declinations (`unsupported`, `clarification_required`, `no_tool`) without forcing analytics tool invocation on unsupported turns, repair query catalogue limits and segment dimensions (`det-006`, `det-007`, `det-013`), route multi-step queries (`react-001`) to the agent loop, and capture comprehensive planner telemetry.

## Starting point
Evaluation on the frozen live baseline (`2026-10-20-golden-baseline`) revealed structural planner and catalogue failures:
- The planner was only offered two tools (`query_taxi_data` and `average_trip_metrics`) despite six authorized governed tools existing in the system.
- `tool_choice` was hardcoded to `"required"` (or Bedrock `{"any": {}}`), forcing the model to hallucinate tool calls on unsupported requests (`uns-*`), resulting in 100% tool choice failure on negative cases.
- Catalogue entry `det-006` had `limit: 20` for ~48 weekday/weekend-hour combinations, truncating evidence and returning incomplete facts.
- Catalogue entries `det-007` and `det-013` erroneously selected `payment_type` as their segment dimension instead of `airport_trip` and `pickup_borough`.
- `react-001` executed a single-step catalogue shortcut rather than routing to the multi-step agent loop required to answer both peak hour borough and dominant zone.

## Decisions
- Raised `MAX_ROWS` from 20 to 64 across `services/dataset_spike/dataset_spike/query_spec.py` and `services/app/app/validation/proposal.py`, enabling complete 48-row results for `det-006`.
- Repaired catalogue entries in `services/app/app/query_catalogue.py`:
  - `det-006`: `limit` set to 50 (within `MAX_ROWS=64`).
  - `det-007`: `segment_dimension` set to `"airport_trip"`.
  - `det-013`: `segment_dimension` set to `"pickup_borough"`.
  - `react-001`: detected via `requires_multi_step_analysis(prompt)` to bypass the 1-step catalogue shortcut in `orchestration/loop.py`, preserving scenario alignment for benchmark replay.
- Fully exposed all 6 governed tools in `TAXI_PROPOSAL_TOOL_SPECS` in `services/app/app/llm.py`:
  - `query_taxi_data`, `average_trip_metrics`, `describe_taxi_dataset`, `list_taxi_dimension_values`, `aggregate_taxi_data`, and `compare_taxi_segments`.
- Relaxed proposal tool choice from forced (`"required"` / `{"any": {}}`) to `"auto"` in `BedrockLLMClient` and `LiteLLMClient`, permitting models to decline unsupported questions directly.
- Introduced typed outcomes in `services/app/app/validation/proposal.py`:
  - `classify_non_tool_outcome(text, question)` returns `unsupported`, `clarification_required`, or `no_tool`.
  - `ProposalValidation.outcome` carries typed outcome metadata.
- Implemented clean typed declination in `services/app/app/orchestration/loop.py`:
  - When the planner declines (`unsupported` or `clarification_required`), emits `run.declined` and persists the assistant message directly without executing MCP analytics tools.
  - Recorded detailed run telemetry: `tool_source` (`catalogue` vs `model`), `planner_outcome`, `selected_tool`, `rejected_outcome`, and `offered_tools`.
  - Preserved `LLMProviderError(code="no_tool_call")` when a model outputs non-declination content without calling a tool on an analytical prompt.

## Verification
- Unit test suite:
  - `uv run --project services/app pytest services/app/tests` -> 375 passed in 17.23s.
  - `uv run --project services/dataset_spike pytest services/dataset_spike/tests` -> 48 passed in 3.05s.
  - `uv run --project tooling/benchmarks --extra app pytest tooling/benchmarks/tests` -> 133 passed in 0.37s.
  - `uv run --project services/mcp pytest services/mcp/tests` -> 20 passed in 5.65s.
  - `uv run --project tooling/evals pytest tooling/evals/tests` -> 89 passed, 2 skipped in 0.68s.
  - `uv run --project services/app pytest tests` (root integration suite) -> 93 passed, 1 skipped in 4.54s.
  - `uv run --project services/app pytest infra/inference/tests` (gateway suite) -> 396 passed in 5.07s.
- Linter & Formatting:
  - `uv run --project services/app ruff check ...` -> all checks passed.
  - `uv run --project services/app black --check ...` -> all modified files formatted.

## PR / merge state
- PR NakulManchanda/ai-analytics-poc#193.
- Branch `feat/186-planner-capability`.

## Lessons
- Forcing tool choice at the API level (`tool_choice="required"`) prevents the model from communicating lack of capability or out-of-scope requests, turning natural declinations into tool hallucinations.
- Canned benchmark scenarios validate catalogue coverage; multi-step questions must be identified explicitly at orchestration time rather than deleted from the underlying catalogue.

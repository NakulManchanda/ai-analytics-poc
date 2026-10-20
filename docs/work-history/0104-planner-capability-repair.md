# 0104 — Planner capability and catalogue repair (#186)

## Goal
Expose all six authorized governed MCP tools to planner model(s) with valid schemas, allow clean typed non-tool declinations (`unsupported`, `clarification_required`, `no_tool`) without forcing analytics tool invocation on unsupported turns, repair query catalogue limits and comparison semantics (`det-006`, `det-007`, `det-013`), route multi-step queries (`react-001`) to the agent loop, and capture comprehensive planner telemetry.

## Starting point
Evaluation on the frozen live baseline (`2026-10-20-golden-baseline`) revealed structural planner and catalogue failures:
- The planner was only offered two tools (`query_taxi_data` and `average_trip_metrics`) despite six authorized governed tools existing in the system.
- `tool_choice` was hardcoded to `"required"` (or Bedrock `{"any": {}}`), forcing the model to hallucinate tool calls on unsupported requests (`uns-*`), resulting in 100% tool choice failure on negative cases.
- Catalogue entry `det-006` had `limit: 20` for ~48 weekday/weekend-hour combinations, truncating evidence and returning incomplete facts.
- Catalogue entries `det-007` and `det-013` segmented the comparison on `payment_type`, so the result had no overall airport/non-airport or Manhattan/Queens figure.
- `react-001` executed a single-step catalogue shortcut rather than routing to the multi-step agent loop required to answer both peak hour borough and dominant zone.

## Decisions
- Raised `MAX_ROWS` from 20 to 64 across `services/dataset_spike/dataset_spike/query_spec.py` and `services/app/app/validation/proposal.py`, enabling complete 48-row results for `det-006`.
- Repaired catalogue entries in `services/app/app/query_catalogue.py`:
  - `det-006`: `limit` set to 50 (within `MAX_ROWS=64`).
  - `det-007` and `det-013`: `comparison_mode: "whole_segment"` with no `segment_dimension` (see review fixes below). The first attempt segmented on the filtered field itself, which can never join (disjoint keys, all-null deltas).
  - `react-001`: detected via `requires_multi_step_analysis(prompt)` to bypass the 1-step catalogue shortcut in `orchestration/loop.py`, preserving scenario alignment for benchmark replay.
- Fully exposed all 6 governed tools in `TAXI_PROPOSAL_TOOL_SPECS` in `services/app/app/llm.py`:
  - `query_taxi_data`, `average_trip_metrics`, `describe_taxi_dataset`, `list_taxi_dimension_values`, `aggregate_taxi_data`, and `compare_taxi_segments`.
- Relaxed proposal tool choice from forced (`"required"` / `{"any": {}}`) to `"auto"` in `BedrockLLMClient` and `LiteLLMClient`, permitting models to decline unsupported questions directly.
- Introduced typed outcomes in `services/app/app/validation/proposal.py`:
  - `classify_non_tool_outcome(text, question)` returns `unsupported`, `clarification_required`, or `no_tool`.
  - `ProposalValidation.outcome` carries typed outcome metadata.
- Implemented clean typed declination in `services/app/app/orchestration/loop.py`:
  - When the planner declines (`unsupported`, `clarification_required` or `no_tool`), emits `run.declined` and persists the assistant message directly without executing MCP analytics tools.
  - Recorded detailed run telemetry: `tool_source` (`catalogue` vs `model`), `planner_outcome`, `selected_tool`, `rejected_outcome`, and `offered_tools`.
  - Preserved `LLMProviderError(code="no_tool_call")` when a model outputs non-declination content without calling a tool on an analytical prompt.

### Review fixes (PR #193 review, three P1 findings)
- **Misaligned comparison (finding 1):** `compare_taxi_segments` joins baseline and comparison on a shared segment dimension, so segmenting on the filtered field yields no matching keys. Added an explicit `comparison_mode` (`by_dimension` default, unchanged; `whole_segment`) across the dataset compiler, MCP tool, app validator, planner schema and loop parser. `whole_segment` aggregates each filtered population independently under the same governed valid-record filter and returns one row per measure (`baseline`, `comparison`, `delta = comparison - baseline`, `delta_pct`) plus stable `baseline_label`/`comparison_label`. An empty side returns nulls and `no_data_sides`, a zero baseline gives a null `delta_pct`. The mode is never inferred; `trip_share` is rejected in this mode. No new tool, no change to filter-basis semantics (#185) or golden values.
- **`no_tool` terminal path (finding 2):** `no_tool` now ends the run like the other non-tool outcomes: no validation of an empty proposal, no MCP call, and a fixed rephrase message instead of ungrounded model prose. The outcome is recorded in telemetry as `planner_outcome`/`rejected_outcome`.
- **Collapsed tool calls (finding 3):** more than one model tool call raises non-retryable `multiple_tool_calls` (Bedrock and proxy paths) instead of becoming an unfiltered `average_trip_metrics({})`. The old all-boroughs fallback for "compare boroughs" is intentionally gone; multi-call execution belongs to the bounded multi-step loop.

## Verification
Re-run after the review fixes (commit f4582cb):
- `uv run --project services/app pytest services/app/tests` -> 391 passed.
- `uv run --project services/dataset_spike pytest services/dataset_spike/tests` -> 56 passed.
- `uv run --project services/mcp pytest services/mcp/tests` -> 23 passed.
- `uv run --project tooling/benchmarks --extra app pytest tooling/benchmarks/tests` -> 133 passed.
- `uv run --project tooling/evals pytest tooling/evals/tests` -> 89 passed, 2 skipped.
- `uv run --project services/app pytest tests` -> 93 passed, 1 skipped.
- `uv run --project services/app pytest infra/inference/tests` -> 396 passed.
- `uv run --project services/app ruff check services/app services/dataset_spike services/mcp` -> all checks passed.

Unverified: the `whole_segment` tests use a synthetic fixture cross-checked against direct SQL, not the real January 2024 parquet; no live MCP-over-HTTP or Bedrock run was made. A stale `services/mcp/.venv` needs `uv sync --project services/mcp --reinstall-package dataset-spike`.

## PR / merge state
- PR NakulManchanda/ai-analytics-poc#193.
- Branch `feat/186-planner-capability`.

## Lessons
- Forcing tool choice at the API level (`tool_choice="required"`) prevents the model from communicating lack of capability or out-of-scope requests, turning natural declinations into tool hallucinations.
- Canned benchmark scenarios validate catalogue coverage; multi-step questions must be identified explicitly at orchestration time rather than deleted from the underlying catalogue.

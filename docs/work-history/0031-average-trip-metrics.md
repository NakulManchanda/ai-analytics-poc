# 0031 — Governed average trip metrics

## Goal

Close issues #58 and #59 with a small, reviewable backlog slice: make the public
borough-comparison prompt executable through a governed MCP tool and restore
readable sample-question chips.

## Starting point

- Branch: `codex/average-trip-metrics`
- Base: `main` after the v1.1 durable-state integration work
- Draft PR: [#60](https://github.com/NakulManchanda/ai-analytics-poc/pull/60)

## Decisions

- Add only `average_trip_metrics(region_name: optional string)`. The MCP server
  executes fixed, parameterized DuckDB queries against the pinned taxi-zone
  lookup; it accepts neither SQL, paths, column names, nor caller-selected
  grouping.
- The default excludes `Unknown`, `N/A`, and `EWR`; a supplied region is
  canonicalized against the same pinned lookup. Invalid regions return a stable
  `invalid_region_name` MCP response with `retryable: false`.
- Keep the application as the owner of model proposals, proposal validation,
  loop budgets, final answers, and durable state. MCP remains LLM-free.
- Set an explicit light background, dark foreground, hover pair, and visible
  keyboard focus outline for sample chips. The default `#e2e8f0`/`#0f172a`
  pair has a 14.2:1 contrast ratio; the hover `#cbd5e1`/`#0f172a` pair has a
  11.5:1 ratio, both exceeding WCAG AA normal-text contrast.

## Verification

- Red: `uv run --project services/app pytest
  services/app/tests/test_governed_query.py::test_exact_borough_comparison_prompt_runs_the_governed_average_metrics_tool -q`
  initially returned HTTP 422 because the new governed proposal was rejected.
- Green focused checks:
  - `uv run --project services/app pytest
    services/app/tests/test_governed_query.py::test_exact_borough_comparison_prompt_runs_the_governed_average_metrics_tool -q`
  - `uv run --project services/mcp pytest services/mcp/tests/test_protocol.py -q`
  - `uv run --project services/dataset_spike pytest
    services/dataset_spike/tests/test_governed_query.py -q`
- Final local suites:
  - `uv run --project services/app pytest services/app/tests -q` — 90 passed.
  - `uv run --project services/mcp pytest services/mcp/tests -q` — 1 passed.
  - `uv run --project services/dataset_spike pytest
    services/dataset_spike/tests -q` — 21 passed.
  - `npm --prefix web test` — 18 passed; `npm --prefix web run build` — passed.
  - Focused Ruff checks for changed Python files — passed.
- GitHub Actions and browser visual verification are intentionally left for the
  draft-PR review cycle; no deployment was performed.

## PR and merge state

- Draft PR: [#60](https://github.com/NakulManchanda/ai-analytics-poc/pull/60)
  (closes #58 and #59).
- Merge and deployment: not performed.

## Lesson

Small backlog fixes can remain reviewable without changing the next learning
milestone when their contracts are explicitly bounded at the MCP boundary.

# Work 0017 — Milestone 8 bounded orchestration loop with budgets

## Goal

Replace the fixed two-call sequence with the real application-owned agent loop that builds bounded context,
queries Bedrock, validates/executes FastMCP tools, records durable state, detects repeated tool loops, and
enforces hard budget limits with terminal state `BUDGET_EXCEEDED`.

## Starting state

`origin/main` at `53e7928` has Milestones 0–7 merged, including governed `query_taxi_data` and DynamoDB
state repository boundaries.

## Decisions

- Implement `ExecutionBudgets` and `BudgetTracker` in `app/orchestration/budgets.py` tracking iterations, LLM calls, tool calls, deadline timeout, token budgets, estimated dollar costs, and result byte limits.
- Implement `OrchestrationLoop` in `app/orchestration/loop.py`:
  - Drives context building, schema context fetching, and model interactions.
  - Detects repeated-equivalent tool calls via `tool:analysis:limit` signatures.
  - Records `Conversation`, `Message`, `Run`, and `RunStep` lifecycle in `StateRepository`.
  - Terminates with status `budget_exceeded` and records `failure_code="budget_exceeded"` upon limit breach.

## Verification

- `make test` passed across all suites: 57 app tests (including 9 orchestration loop budget & loop tests), 1 MCP test, 13 dataset spike tests, 12 infra/port tests, and 4 React UI tests.
- Formatting (Black), linting (Ruff), and bytecode compilation (`compileall`) passed cleanly.

## Pull request and merge state

Branch `feat/m8-orchestration-loop` tracks [issue #25](https://github.com/NakulManchanda/ai-analytics-poc/issues/25).

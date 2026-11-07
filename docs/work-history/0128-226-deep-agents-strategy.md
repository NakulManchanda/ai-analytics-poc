# 0128 — Opt-in Deep Agents strategy, single turn (#226, parent #224)

## Goal
Add `AGENT_STRATEGY=deep_agents`: the Deep Agents library chooses tools, calls the existing read-only MCP tools and answers over the same LiteLLM route, so #228 can compare it with the bespoke loop (`manual`) on cost per correct answer.

## Starting point
`manual|crewai` only. The `crewai` strategy replaces just the final answer step; the manual loop still proposes and runs the tool, so it is not a tool-selection comparison. The issues call the control `react`; the code value is `manual` and is unchanged.

## Decisions
- Separate dispatch, not a branch: `OrchestrationLoop.execute()` calls `execute_deep_agent_run` (new `orchestration/deep_agents_run.py`) when the strategy is `deep_agents`; `_execute_loop` is untouched, so manual and crewai carry no regression risk.
- Same run contract: run events, RunStep rows, durable tool/assistant messages, Run row, budgets (BudgetTracker), cancellation, timeout, terminal statuses and failure codes; `strategy: deep_agents` in run metadata.
- Requires `LLM_PROVIDER=litellm`. Per-call LiteLLM correlation headers (run, call, eval run, conversation, trace context) and billed-cost header match the manual path; cost helpers are duplicated, not refactored out of `_execute_loop`.
- Tool exposure: Deep Agents 0.7.32 exposes `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`, `task` by default and `permissions=` does not remove them. An allowlist middleware shows the model only the four governed MCP tools plus `write_todos` (`TodoListMiddleware`, which 0.7.32 does not ship by default) and refuses any other call.
- Tool schemas come from the MCP server (`list_tools`), cached once per process and cleared after an unknown-tool error; not hand-copied.
- MCP schemas made specific with pydantic models published through `WithJsonSchema` (filters, order_by, dimension/measure/comparison_mode enums). Runtime validation and error envelopes are unchanged (`dataset_spike` validators remain the source of truth).
- Dependencies `deepagents==0.7.32`, `langchain-openai`; imported lazily so the default path never loads them.

## Verification
- `services/app`: `uv run pytest` 523 passed; `services/mcp`: 57 passed; `black --check` and `ruff check` clean for both projects.
- Stress: 15 runs of the new run/strategy tests, no hang or failure. One earlier full-suite run under load had 1 failure and one loop run stalled; neither reproduced and the cause was not identified.
- No live LiteLLM, Bedrock or MCP server call was made.

## Known differences and limits
- Not streamed: `answer.completed` only, no `answer.delta`; ttft reported as `non_streaming_blocking`.
- `apply_grounding`, voice (`answer.audio`), the catalogue short-circuit and the model-policy `_governed` layer are not applied (the monthly Bedrock allowance reservation is).
- `write_todos` calls produce no tool events or steps; hallucinated tools are refused before any callback.
- A tool failure returns to the model and the run continues (the manual loop fails the run).
- Single turn: no conversation history restore yet (next slice, #110 axis). Phoenix/OTEL parity spans are a later slice (observability contract in #226).
- Unverified: proxy native tool calling for the planner alias, the real billed-cost header, and the real `ChatOpenAI` request path through the httpx hooks.
- The MCP service must be rebuilt for the richer schemas to reach the app.

## PR / merge state
Branch `feat/226-deep-agents-strategy`; draft PR referencing #226 (does not close it).

## Lessons
- A framework's default tool set is a capability grant; filter what the model sees and refuse the rest.
- Publishing a richer schema via `WithJsonSchema` avoids changing runtime validation behavior.

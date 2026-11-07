# 0132 — Deep Agents Phoenix/OTEL parity (#226, parent #224)

## Goal
Make a Deep Agents run and a manual run comparable in Phoenix (one trace root per run, distinguishable by strategy, model calls visible, MCP and LiteLLM spans in the same trace) without a second backend or duplicate instrumentation, and make a run with a broken trace context distinguishable so #228 can exclude it.

## Starting point
The manual path emits one `ai.run` span (attributes `ai.run_id`, `ai.conversation_id`, `session.id`, `ai.turn_type`, `gen_ai.request.model`, `ai.eval_run_id`), one `ai.llm_call` span event per model call via `_TracedCalls`, and MCP/LiteLLM spans join through trace context. There are no per-tool spans on any strategy. The Deep Agents recorder already used `_TracedCalls`.

## Decisions
- Root attributes on every strategy, additive: `agent.strategy` (manual | crewai | deep_agents), `agent.framework` (bespoke | crewai | deepagents), and `agent.step_count` set in `execute()` after the run. Existing attribute names are unchanged and no second eval-run-id key was added. `_execute_loop` is untouched.
- Deep Agents only: `ai.plan.updated` span event when `write_todos` runs, with counts only (`todos_total`, `pending`, `in_progress`, `completed`), never the todo text; `ai.context.compacted` (`cutoff_index`, `messages_total`) when the allowlist middleware observes deepagents' summarization state change. Not proven end to end: the hook reads deepagents' private `_summarization_event` state key at the next model call, so it depends on the installed deepagents version and misses a compaction that is followed by no further model call. Errors in the hook are logged and swallowed.
- Run metadata `trace: {context_valid, trace_id, propagation_failures}` (the failures are the existing correlation header-injection counter), so a run with a tracing problem is visible and can be excluded in evaluation.
- Tests (in-memory exporter, no network): the proxy request `traceparent` and a tool call's injected headers carry the `ai.run` trace id (the worker thread sees the run's context); a no-op tracer still completes with `context_valid: false`; forced injection failure is counted; `ai.llm_call` event count equals recorded calls with unique ids; the manual span carries the new attributes.

## Verification
- `services/app` full suite passed on the rebased branch (see PR description for the count); new `test_deep_agents_otel.py` (9 tests); affected files repeated 10x without flakes; black and ruff clean.
- Not verified: a real Phoenix smoke, a real MCP transport (a fake MCP records `propagate.inject`), real summarization.

## Known limits
No per-tool spans on any strategy (not invented). Compaction event depends on a private deepagents state key. `write_todos` still produces no run events or steps, only the span event. Reconciling model-call counts with LiteLLM spend rows and billed costs is a live check (below).

## PR / merge state
Branch `feat/226-phoenix-parity`, stacked on `feat/226-model-policy` (PR #240); PR references #226.

## Lessons
- A hook on a framework's private state is a convenience, not a contract: label it as unproven and make failures non-fatal.

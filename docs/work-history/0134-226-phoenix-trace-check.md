# 0134 — Phoenix trace-continuity check for eval runs (#226, parent #224)

## Goal
Before #228 compares strategies from Phoenix traces, prove from real data that every eval run has one continuous trace (app, LiteLLM, MCP) with the strategy attributes, and that a missing or broken trace cannot pass silently.

## Starting point
#241 added `agent.strategy`, `agent.framework` and `agent.step_count` to `ai.run`, but trace continuity was only tested with in-memory exporters and a fake MCP. The live smoke was a manual, unwritten step. Built on #242 (billing reconciliation) because both add `evals` run commands and Make targets.

## Decisions
- Shape taken from the running Phoenix, not guessed: `GET /v1/projects/<project>/spans?trace_id=<id>` returns `{data, next_cursor}`; spans have `name`, `context.{trace_id,span_id}`, `parent_id`, `attributes` and no service name, so spans are identified by name: `ai.run`, `litellm_proxy_request`, `mcp.tool.execute`. An unknown trace returns empty data and an unknown project 404.
- `evals.trace_check.check_traces` is pure over capture records and an injected fetch function; `phoenix_fetch` uses stdlib `urllib` (no new dependency) and raises on any transport error.
- Per run: capture has a `trace_id`; at least one span; exactly one `ai.run`; every span descends from it; root has `ai.run_id` (equal to the capture run_id), `ai.status`, `agent.strategy`, `agent.framework`, `agent.step_count`; `litellm_proxy_request` count >= capture model calls and `mcp.tool.execute` >= capture tool calls (fallbacks may add LiteLLM spans, so `>=`).
- Review fix (P2): the unique `ai.run` must have no parent, and any span whose ancestor chain hits a missing parent or a cycle counts as detached (the earlier walk stopped at a missing parent and returned that span's own name). Capture records of one run with different non-null `trace_id`s are a problem and are not queried, instead of silently using the first.
- Exit 4 with `PROBLEM:` lines on any gap, 2 if Phoenix cannot be queried. `evals run-trace-check` / `make eval-trace-check` write `report/trace_check.json`.

## Verification
- `uv run --project tooling/evals pytest tooling/evals/tests`: 199 passed, 2 skipped; black and ruff clean.
- Read-only check against the live local Phoenix on a recorded 15-span trace from before #241: parentage intact, 2 LiteLLM and 1 MCP tool span found; the only problem reported was the missing `agent.*` attributes, as expected for that older run.
- Not verified: a Deep Agents run (none exists in this Phoenix yet), a cancelled run's trace, a trace with LiteLLM fallbacks. These come from the user's live eval run.

## Known limits
`mcp.tool.execute` is compared with capture `tool_call` records; if a tool call is served without MCP the check flags it. `ai.context.compacted` and plan events are best-effort and are not required. Phoenix retention/ingest delay can make a fresh run look empty: run it after the traces appear.

## PR / merge state
Branch `feat/226-trace-check`, rebased onto `main` after PR #242 merged; PR references #226.

## Lessons
Inspect the real export before writing a checker: the REST payload has no service name, so span names are the stable identity.

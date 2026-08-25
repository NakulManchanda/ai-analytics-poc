# Work History Entry: 0027

## Goal

Deliver the truthful working-context and blocking-run telemetry slice tracked by GitHub issue #46.

## Starting Point

The unified orchestration loop emitted a transient context event and partial latency data, but did not retain that context or phase telemetry in durable state. Non-budget orchestration failures could also leave a durable run marked `in_progress`.

## Changes

- Build `context.reduced` from the persisted current message identity, repository message history, sanitized MCP schema/tool result, and live budget tracker.
- Persist the reduced working context as a `context_reduced` RunStep so SSE reconciliation can reproduce it after transient Redis events disappear.
- Record end-to-end, proposal LLM, MCP/tool, and final-answer LLM latency, token totals, estimated cost, and explicit blocking-mode TTFT unavailability in durable Run metadata.
- Include persisted telemetry in live terminal events and reconstructed terminal SSE events, while preserving the established synchronous response contract.
- Mark every non-budget `OrchestrationError` run as `failed` with completion metadata, truthful failure code, and a terminal `run.failed` event.
- Use one shared context/terminal event payload contract so live Redis events and durable SSE reconstruction retain matching telemetry fields.

## Decisions

- TTFT is intentionally represented as `{ "available": false, "reason": "non_streaming_blocking" }`; v1.1 does not infer a value without token streaming.
- The reducer uses the durable current-message ID in the runtime path, avoiding ambiguity when a user repeats an earlier prompt verbatim.
- Redis remains transient: Run and RunStep persistence contains the information needed for SSE reconciliation.
- Reconstructed terminal payloads now derive `total_tokens` from durable input/output totals and use persisted end-to-end telemetry for `latency_ms`; context steps retain the query row count needed to match the live event.

## Verification

- Red/green TDD checks verified the missing current-message identity contract, durable context/telemetry reconstruction, persisted phase telemetry, and non-budget failed-run transition before implementation.
- `uv run pytest tests/test_context_reducer.py tests/test_orchestration_loop.py tests/test_events.py -q` from `services/app` — 21 passed.
- `uv run ruff check app tests` from `services/app` — passed.
- `uv run black --check app tests` from `services/app` — passed.
- Review-fix parity check: `uv run pytest tests/test_events.py -q` from `services/app` — 8 passed.

## PR and Merge State

- Branch: `codex/v11-truthful-telemetry`
- Tracking issue: #46
- Draft pull request: [#52](https://github.com/NakulManchanda/ai-analytics-poc/pull/52); not merged or deployed.

## Limitations

- This is intentionally blocking v1.1 execution: no provider streaming, token deltas, cancellation, frontend work, or AWS infrastructure changes are included.

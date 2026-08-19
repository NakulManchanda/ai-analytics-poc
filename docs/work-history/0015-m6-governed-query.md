# Work 0015 — Milestone 6 governed analytical query

## Goal

Add one governed `query_taxi_data` FastMCP tool and extend the fixed application-owned flow so
three deterministic analytics questions use dataset/schema context, validated structured requests,
bounded DuckDB results, and a final model answer.

## Starting state

`origin/main` at `c024d9a` has Milestones 0–5 merged. It supports a fixed two-model-call flow over
`get_dataset_profile`, but has no analytical query contract or bounded query result envelope.

## Decisions

- Prefer an allowlisted structured analysis enum (`top_pickup_zones`, `trip_volume_by_hour`, `average_distance_by_weekday`) and bounded `limit` (1..20) rather than accepting SQL or filesystem paths from either the user or model.
- Keep the execution path fixed to two model calls and one MCP tool call; no general loop, DynamoDB, Redis/SSE, or worker is added.
- Supply the MCP schema resource (`dataset://nyc-taxi/schema`) to proposal generation and validate the exact structured request before DuckDB execution.
- Keep internal DuckDB SQL fixed, SELECT-only, single-statement, and hard-bounded by time, row count, and serialized result bytes (<= 8 KiB).
- Run DuckDB queries inside isolated killable worker processes with a hard deadline to prevent lingering executions or resource exhaustion on timeout. Disable DuckDB external access (`SET enable_external_access = false`) after materializing pinned input views.
- Result envelopes include `columns`, bounded `rows`, `row_count`, `execution_duration_ms`, opaque non-empty `query_id`, and `truncated` boolean.

## Verification

- `make test` passed: app (40), MCP (1), dataset spike (13), infra and port checks (12), and React (4) tests plus Vite production build.
- Formatting, linting, and bytecode compilation passed cleanly: Black, Ruff, and `compileall` across all python packages.
- Automated Docker Compose smoke (`make compose-smoke`) passed cleanly using isolated project and ephemeral ports.
- Fake-backed tests cover valid query paths across all three starter questions, schema propagation, rejection of unsafe/extra fields, rejection of oversized limits, and malformed MCP responses.
- `make m6-bedrock-smoke` added with STS account identity verification for safe, explicitly opt-in real Bedrock execution.

## Pull request and merge state

Draft [PR #33](https://github.com/NakulManchanda/ai-analytics-poc/pull/33), branch
`feat/m6-governed-query`, tracks [issue #24](https://github.com/NakulManchanda/ai-analytics-poc/issues/24).

## Lessons

- Structuring the query tool as a closed enum of parameterized analyses completely eliminates SQL injection risks while satisfying analytical questions reliably.
- Isolating DuckDB inside a killable subprocess guarantees deterministic timeout handling without relying on thread interruption or DuckDB internal cancellation.

# 0060 — MCP W3C trace propagation and DuckDB child spans

## Goal

Deliver issue #118 as the second bounded observability slice from ADR 0007:
continue a synchronous application `ai.run` trace through the existing
Streamable HTTP MCP boundary, then expose safe MCP protocol, tool, and DuckDB
child spans in the local Collector and Jaeger path.

## Starting point

O1 was merged as PR #117 at `fe81b93`. It created an opt-in, fail-open
application tracing runtime, the local Collector/Jaeger overlay, and one safe
`ai.run` root span. The MCP client and service did not yet propagate W3C
context, initialize an independent MCP tracing runtime, or create MCP/DuckDB
child spans.

## Decisions and changes

1. Made the existing app-to-MCP Streamable HTTP transport explicit in
   `services/app/app/mcp_client.py`, then use the standard propagator to inject
   W3C context only when a valid current span exists. This preserves the MCP
   protocol and avoids synthetic headers when tracing is inactive.
2. Added a separate, opt-in, fail-open MCP SDK runtime in
   `services/mcp/mcp_server/telemetry.py`. It has its own provider and OTLP
   exporter configuration; missing configuration or runtime initialization
   failure leaves the MCP service usable without trace export.
3. Added a small MCP ASGI entry point that supplies the MCP tracer to FastMCP.
   FastMCP middleware extracts the inbound W3C carrier and creates a SERVER
   `mcp.request` span, preserving the parent trace across the HTTP boundary.
4. Added semantic FastMCP child spans: `mcp.tool.execute`,
   `mcp.resource.read`, and one `duckdb.query` span per governed query call.
   The query span is not created until fixed analysis and bounded limit inputs
   have been validated.
5. Preserved the current span through the existing one-worker executor with a
   fresh `contextvars.copy_context()` per submission in
   `services/app/app/orchestration/loop.py`. This narrowly fixes the synchronous
   cancellation wrapper's otherwise lost active OTEL context; cancellation,
   return, error, worker, and Redis behavior remain unchanged.
6. Extended `scripts/smoke/16_observability.sh` to fetch the Jaeger trace by
   the returned `ai.run` trace identifier and prove the correlated hierarchy
   `ai.run` → `mcp.request` → `mcp.tool.execute` → `duckdb.query` using returned
   span IDs rather than response ordering.
7. Added project-scoped `observability-dev-*` Make targets for the manual
   walkthrough. They use `ai-analytics-119`, web port `13000`, and Jaeger port
   `16686` by default (all overridable), expose the existing JSONL/EMF metrics
   and bounded app/MCP/Collector logs, and tear down only that Compose project
   with its orphans.

## Privacy and signal ownership

The spans use only bounded operational attributes. The root retains bounded run,
conversation, turn, model, and terminal-status dimensions. MCP request spans
record fixed `rpc.system` and allowlisted `rpc.method`; tool/resource spans use
fixed tool names or the fixed schema resource URI; DuckDB spans record fixed
database/tool identifiers and, only for the governed analysis tool, allowlisted
analysis plus bounded row limit. Error status is retained with no description
and without recording raw exception events.

No span attributes, event attributes, status descriptions, or resource
attributes include prompts, raw SQL, complete tool arguments, region input,
result rows, resource content, model output, headers, credentials, secrets, or
other unrestricted user content. The smoke checks prompt-like,
fixture-row, SQL-like, and credential-like sentinels are absent from the Jaeger
trace JSON. This keeps traces additive to—not replacements for—Redis/SSE
workflow events, CloudWatch EMF, local JSONL metrics, structured logs, and
durable DynamoDB state, as defined by ADR 0007.

## Verification

- App propagation and orchestration coverage:
  `uv run --project services/app pytest services/app/tests/test_mcp_client.py services/app/tests/test_governed_query.py services/app/tests/test_orchestration_tracing.py -q`
  — 13 passed (one existing third-party Authlib deprecation warning).
- MCP tracing/runtime coverage:
  `uv run --project services/mcp pytest services/mcp/tests/test_telemetry.py -q`
  — 10 passed; `uv run --project services/mcp pytest services/mcp/tests/test_protocol.py services/mcp/tests/test_telemetry.py -q`
  — 14 passed; and `uv run --project services/mcp pytest services/mcp/tests -q`
  — 16 passed (each with the same existing warning where reported).
- Context-preservation regression and cancellation coverage:
  `uv run --project services/app pytest services/app/tests/test_orchestration_tracing.py services/app/tests/test_orchestration_cancellation.py -q`
  — 6 passed.
- Formatting and linting passed with Black and Ruff for the changed app and MCP
  files; `uv lock --project services/mcp` and `uv lock --project . --check`
  passed.
- `docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet`
  passed. An isolated Compose startup built a healthy MCP service and verified
  that Collector ingestion ports had no host mapping.
- `bash -n scripts/smoke/16_observability.sh`, `make -n observability-up`, and
  `make -n observability-down` passed. `make observability-smoke` passed using
  isolated dynamic ports and cleanup, and found the correlated Jaeger trace
  with the expected safe attributes and privacy sentinels absent.
- `git diff --check` passed for the implementation work. The documentation
  safety scan and final whitespace check are recorded with this work-history
  update.
- Live manual verification on 2026-09-24 used the project-scoped defaults:
  `observability-dev-up`, readiness checks, `observability-dev-info`,
  `observability-dev-ask`, `observability-dev-metrics`,
  `observability-dev-logs`, Jaeger trace polling, and
  `observability-dev-down`. The request completed with HTTP 200; the matching
  JSONL metric was completed; and one Jaeger trace joined `ai.run`,
  `mcp.request`, `mcp.tool.execute`, and `duckdb.query` across
  `ai-analytics-app` and `analytics-mcp`. Teardown left no containers or
  network for `ai-analytics-119`.

## PR and issue state

- Issue: #118
- Draft PR: [#119](https://github.com/NakulManchanda/ai-analytics-poc/pull/119)
- Branch: `codex/o2-mcp-tracing`
- State: review-ready. The branch was rebased onto `main` after workflow
  guidance PR #127 merged. The independent review's privacy, test, FastMCP
  robustness, and work-history findings were addressed; exact-head CI passed;
  and the same independent review session found no remaining substantive code
  findings.

## Limitations and next slice

- Redis envelopes and the worker do not yet propagate or restore W3C context.
- Bedrock spans, Langfuse export, AWS ADOT/X-Ray export, and observability
  dashboards are deferred.
- OTEL logs and metrics are not introduced, and CloudWatch EMF/local JSONL
  metrics remain unchanged.
- The local Collector and Jaeger stack is only a local, transient validation
  backend; it is not a deployment or retention design.

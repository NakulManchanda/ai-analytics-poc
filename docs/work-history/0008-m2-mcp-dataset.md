# Work 0008 — Expose the Milestone 2 dataset through MCP

## Milestone

Milestone 2 — Dataset spike, isolated from AI

## Branch

`feat/m2-mcp-dataset`

## Worktree

`.worktrees/m2-mcp-dataset`

## Goal

Expose the pinned NYC Taxi dataset schema and fixed profile through the existing FastMCP service.

## Starting state

PR #12 merged the checksum-pinned dataset spike and DuckDB profile. The MCP service exposed no
dataset resources or tools.

## Decisions

- Reuse the dataset spike's fixed profile rather than introduce SQL or a second DuckDB layer.
- Keep tests fixture-only by injecting a fixed profile at the MCP boundary.

## TDD evidence

The MCP contract test was added before `build_mcp` existed. It first failed because the MCP project
did not depend on `dataset_spike`, then (after adding the direct local dependency) failed because
`build_mcp` was absent. A second RED assertion established that initialization must load the pinned
profile once per server lifespan rather than reopen DuckDB for both the resource and tool.

## Verification

- `uv run --project services/mcp pytest services/mcp/tests/test_protocol.py` — 1 passed.
- `uv run --project services/mcp black --check services/mcp/mcp_server services/mcp/tests` — passed.
- `uv run --project services/mcp ruff check services/mcp/mcp_server services/mcp/tests` — passed.
- `uv run --project services/mcp python -m compileall -q services/mcp/mcp_server` — passed.
- `MCP_PORT=8011 make mcp-smoke` — schema resource and profile tool discovered from a fresh local
  MCP process.
- In-process live profile call — 2,964,624 trip rows and 265 zone rows returned from the pinned
  dataset.
- `docker build -f services/mcp/Dockerfile -t ai-analytics-mcp:m2-dataset .` — passed.

## Pull request and merge

- Draft PR: https://github.com/NakulManchanda/ai-analytics-poc/pull/17
- Merge: not authorized.

## Lessons

- Keep startup-loaded DuckDB data behind a single fixed MCP surface until the governed query-tool
  milestone defines typed, bounded query inputs and outputs.

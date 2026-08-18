# Work 0004 — Milestone 1 empty MCP service

## Goal

Establish a separately runnable FastMCP service with a real initialize handshake and empty
`tools/list` and `resources/list` responses.

## Starting state

Started from the merged M0 health endpoint on branch `feat/m1-empty-mcp` in its own worktree.

## Decisions

- Use the standalone `fastmcp` package pinned to the supported v2 line (`>=2,<3`).
- Use FastMCP's documented Streamable HTTP transport at `/mcp` on port 8001.
- Keep the service empty: no tools, resources, dataset, DuckDB, LLM, Redis, AWS, or React.
- Pin uv 0.9.26 and run the image as non-root UID 10002.

## TDD evidence

The protocol contract test was written first. Its RED run failed because the new MCP project did
not yet exist and `pytest` was unavailable for `services/mcp`. After the minimal project and
FastMCP server were added, the focused test passed.

## Verification

- Focused in-process MCP protocol test: passed (1 test)
- FastMCP live HTTP smoke: passed (`tools=[]`, `resources=[]`)
- Cumulative `make smoke` with a managed M0 app: passed
- `make test`: passed (1 app test, 1 MCP test)
- Black format checks: passed
- Ruff lint checks: passed
- Python compile check: passed
- Docker image build: passed (`ai-analytics-mcp:m1`)
- Docker live MCP protocol check: passed
- Docker runtime user: `mcpuser` (`uid=10002`)

## Pull request and merge

Draft PR: https://github.com/NakulManchanda/ai-analytics-poc/pull/2

## Lessons

The in-process FastMCP client contract verifies initialization and discovery without adding an
application-to-MCP coupling before that integration is requested.

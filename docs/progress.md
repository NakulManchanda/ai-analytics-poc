# Current milestone

Milestone 1 — empty FastMCP service

## Status

IMPLEMENTED — awaiting review and merge

## Acceptance criteria

- [x] M0 health endpoint remains covered by the cumulative smoke
- [x] Empty FastMCP service starts independently on port 8001
- [x] MCP initialize handshake succeeds
- [x] `tools/list` returns an empty list
- [x] `resources/list` returns an empty list
- [x] FastMCP container runs as a non-root user
- [ ] Pull request reviewed and merged

## Decisions

- FastMCP is a separate service under `services/mcp`; it contains no LLM, dataset, DuckDB, Redis, or AWS code.
- FastMCP's documented HTTP transport is used at `/mcp` on port 8001.
- The MCP service pins uv 0.9.26 and runs as UID 10002 in its container.

## Known limitations

- This milestone intentionally contains no dataset, DuckDB, React, Redis, AWS, or LLM integration.

## Next milestone

Next milestone: Milestone 2 — reproducible NYC TLC Parquet dataset spike with DuckDB.

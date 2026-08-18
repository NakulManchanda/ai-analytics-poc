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

The MCP contract test was added before `build_mcp` exists and is expected to fail at import time.

## Verification

- Pending implementation.

## Pull request and merge

- Draft PR: pending.
- Merge: not authorized.

## Lessons

- Pending completion.

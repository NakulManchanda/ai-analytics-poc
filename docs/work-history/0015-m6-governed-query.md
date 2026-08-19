# Work 0015 — Milestone 6 governed analytical query

## Goal

Add one governed `query_taxi_data` FastMCP tool and extend the fixed application-owned flow so
three deterministic analytics questions use dataset/schema context, validated structured requests,
bounded DuckDB results, and a final model answer.

## Starting state

`origin/main` at `c024d9a` has Milestones 0–5 merged. It supports a fixed two-model-call flow over
`get_dataset_profile`, but has no analytical query contract or bounded query result envelope.

## Decisions

- Prefer an allowlisted structured analysis enum to accepting SQL from either the user or model.
- Keep the execution path fixed to two model calls and one MCP tool call; no general loop is added.
- Supply the MCP schema resource to proposal generation and validate the exact structured request
  before DuckDB execution.
- Keep internal DuckDB SQL fixed, SELECT-only, single-statement, and hard-bounded by time, row count,
  and serialized result bytes.

## Verification

- Baseline `make test` passed: app 29, MCP 1, dataset 5, root 12, and React 4 tests plus production build.
- TDD red/green evidence and milestone smoke results will be recorded as implementation proceeds.

## Pull request and merge state

Branch `feat/m6-governed-query` tracks
[issue #24](https://github.com/NakulManchanda/ai-analytics-poc/issues/24). Draft PR pending.

## Lessons

- Pending implementation and review.

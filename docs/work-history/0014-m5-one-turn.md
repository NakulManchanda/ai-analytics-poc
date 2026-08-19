# Work 0014 — Milestone 5 one-turn LLM-to-MCP execution

## Goal

Deliver exactly one governed analytics sequence: a first application-owned Bedrock call proposes
the fixed `get_dataset_profile` tool, FastAPI validates and invokes it through the existing
FastMCP HTTP boundary, then a second Bedrock call produces the final answer.

## Starting state

`origin/main` at `ec4a46d` had merged Milestones 0–4. It exposed the fixed dataset-profile MCP
tool and one Bedrock-backed `/api/ask` call, but the browser prompt was disabled and no tool
execution path existed.

## Decisions

- This milestone permits no general loop: it is bounded to two LLM calls and one MCP call.
- Only the exact `get_dataset_profile` proposal with no arguments may cross the MCP boundary.
- Normal verification uses fakes; the real Bedrock smoke remains separately opt-in and paid.

## Verification

In progress. The clean starting backend, MCP, dataset, and Terraform test suites passed. The
React package had not yet installed its `node_modules` in this fresh worktree, so the baseline
`make test` stopped at the missing `vitest` executable; dependency installation precedes the
frontend baseline rerun.

## Pull request and merge state

Draft PR pending creation for [issue #22](https://github.com/NakulManchanda/ai-analytics-poc/issues/22).

## Lessons

To be completed with the final verification record.

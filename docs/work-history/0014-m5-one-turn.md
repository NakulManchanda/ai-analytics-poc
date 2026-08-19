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
- Docker Compose uses a deterministic `LLM_PROVIDER=fake` exclusively for no-cost browser/API/MCP
  integration coverage. The default runtime remains Bedrock, with only the existing `ai-app`
  role permitted to invoke it.
- The result sanitizer allows only the known fixed-profile fields, caps the serialized payload at
  8 KiB, bounds column and day/zone-row counts, and rejects non-finite/unknown values before the
  second model call.

## Verification

- TDD red: `uv run --project services/app pytest services/app/tests/test_one_turn.py -q` failed
  because `create_app()` did not yet accept an injected MCP boundary. The focused sequence tests
  passed after the bounded router and adapter were added.
- `make test` passed: app (29), MCP (1), dataset spike (5), Terraform static assertions (9), and
  React (4) tests; the React production build, Ruff, and Black checks passed. FastMCP emitted its
  existing Authlib deprecation warning only.
- Cumulative health, FastMCP discovery, and pinned-dataset smoke passed with a locally started
  app process. `WEB_PORT=3001 make compose-smoke` passed the browser → Nginx → app → MCP prompt
  path while preserving an already-running preview on port 3000.
- `aws sts get-caller-identity` confirmed account `107207236011`; then
  `RUN_BEDROCK_SMOKE=1 MCP_PORT=8002 make m5-bedrock-smoke` passed with exactly two Nova Micro
  calls, one MCP tool call, 1,132 total tokens, and 706 ms aggregate Bedrock latency.

## Pull request and merge state

Draft [PR #23](https://github.com/NakulManchanda/ai-analytics-poc/pull/23), branch
`feat/m5-one-turn`, tracks [issue #22](https://github.com/NakulManchanda/ai-analytics-poc/issues/22).
Exact-head CI and Copilot review remain pending.

## Lessons

- A fixed vertical slice benefits from making the tool proposal interface explicit: the model
  cannot name a future query tool or provide profile arguments by accident.
- Local Compose integration needs an explicit fake provider to cover the visible browser path
  without silently spending Bedrock money; the separate opt-in smoke proves the real boundary.

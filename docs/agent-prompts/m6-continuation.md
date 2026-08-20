# M6 continuation prompt for Claude or Gemini

Copy everything below into one external agent. Use only one write agent at a time.

---

You are helping complete Milestone 6 in the private repository
`/Users/nakulmanchanda/dev/ai_app_poc`.

Read these sources before acting:

1. `/Users/nakulmanchanda/dev/ai_app_poc/AGENTS.md`
2. `/Users/nakulmanchanda/dev/ai_app_poc/ai_analytics_poc_requirements_aws_v5.md`, especially
   “Milestone 6 — Governed analytical query tool”
3. GitHub issue `#24` and draft PR `#33`
4. `/Users/nakulmanchanda/dev/ai_app_poc/.worktrees/m6-governed-query/docs/progress.md`
5. The current diff and recent commits on branch `feat/m6-governed-query`

The active write worktree is:
`/Users/nakulmanchanda/dev/ai_app_poc/.worktrees/m6-governed-query`.

Default to **read-only review** because the primary Codex agent may be editing that worktree. Do not
edit, commit, push, merge, change GitHub, mutate AWS, or stop/reuse another task's services unless
the user explicitly tells you that write ownership has been handed to you. If write ownership is
explicitly handed off, first confirm the worktree is clean or understand every existing change;
never discard or overwrite it.

Current intended contract:

- The caller supplies only `{analysis, limit}` to `query_taxi_data`; it never supplies SQL, paths,
  byte caps, deadlines, table names, or functions.
- `analysis` is exactly one of `top_pickup_zones`, `trip_volume_by_hour`, or
  `average_distance_by_weekday`; `limit` is an integer from 1 through 20.
- MCP constructs fixed SELECT-only statements over checksum-pinned local inputs. It materializes
  those inputs before disabling DuckDB external access.
- Query execution has a hard killable-process deadline and returns a maximum 8 KiB envelope with
  `columns`, bounded `rows`, `row_count`, `execution_duration_ms`, an opaque non-empty `query_id`,
  and `truncated`.
- FastAPI reads the existing schema resource, supplies it to the first Bedrock call, validates the
  exact structured tool proposal, performs exactly one query tool call, validates the envelope,
  and gives it to the second Bedrock call. No general loop, DynamoDB, Redis/SSE, worker, or user SQL.
- Normal tests and Compose use fakes. Any real Bedrock smoke must remain explicitly opt-in and must
  verify AWS account `<aws-account-id>` immediately before the paid call.

Completed checkpoint on PR #33:

- Baseline `make test` passed.
- Commit `9e47e53` adds the structured MCP query tool and dataset executor.
- Dataset focused tests cover all three analyses, invalid analysis/limits, byte truncation, and a
  hard timeout; MCP protocol coverage includes the new tool.
- A separate Codex Terra-medium review found and prompted replacement of an unbounded thread cleanup
  with killable process isolation.

Remaining work to inspect or complete after explicit write handoff:

1. Finish the FastAPI/Bedrock/MCP-client M6 contract test-first.
2. Update the React copy from “profile” to “governed query” without adding future UI architecture.
3. Add deterministic local smoke for the three starter questions and an explicitly opt-in real
   Bedrock smoke for at least one question.
4. Run focused tests, `make test`, Black, Ruff, compile/import checks, Docker/Compose smoke, and
   `git diff --check`.
5. Update README, progress, work history, and PR #33 checklist with exact evidence.
6. Request exact-head Copilot review. Do not merge until checks pass and the user/primary orchestrator
   authorizes it.

For a read-only assignment, return prioritized findings with file/line evidence and the smallest
specific fix. For an explicitly authorized write assignment, preserve strict TDD red → green
evidence, commit a coherent checkpoint, push promptly to PR #33, and report exact commands/results.

---

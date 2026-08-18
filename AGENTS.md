# AI Analytics POC — agent instructions

This file is the canonical project guidance. Tool-specific files (`CLAUDE.md`, `GEMINI.md`,
and `.github/copilot-instructions.md`) must stay thin and refer here rather than duplicate it.

## Scope and source of truth

- Build one milestone at a time. Work only on the milestone explicitly requested; do not
  pre-build future architecture. Read `docs/implementation-plan.md`, `docs/progress.md`, and
  the local `ai_analytics_poc_requirements_aws_v5.md` before changing implementation.
- Prefer the smallest externally meaningful vertical slice. Preserve the requirements document
  and its AWS-only, deliberately small POC boundaries; do not add Kubernetes, Kafka/Kinesis,
  RDS, EFS, OpenSearch, a vector database, a warehouse, or a second hosting platform.
- Keep FastAPI orchestration and FastMCP as separate services once MCP exists. The application
  server owns every LLM call, loop budget, and durable state; the MCP server never calls an LLM.
  Redis is transient coordination only, never durable conversation state.

## Change workflow

- After bootstrap, use a dedicated branch and project-local worktree for every change. Keep the
  main checkout untouched; use `.worktrees/<topic>` and a descriptive branch name.
- Make the smallest coherent commit, push the branch early, and open a **draft** PR as soon as
  the change is reviewable. Keep the PR description current with context, decisions, tests, and
  known limitations; finish updates on that same PR. Do not merge without explicit authorization.
- Every post-bootstrap PR gets a monotonically numbered entry under `docs/work-history/` covering
  goal, starting point, decisions, verification, PR/merge state, and lessons. Update
  `README.md` and `docs/progress.md` when the active milestone requires it.
- Do not rewrite or discard unrelated user work. Never commit credentials, tokens, private keys,
  `.env` values, or other secret material; use placeholders and ignored local configuration.

## Implementation and interoperability

- Keep provider and tool boundaries explicit: model calls belong in the application, tool calls
  cross the MCP boundary, and durable state is authoritative over Redis events. Do not expose
  hidden chain-of-thought; expose only bounded, user-meaningful working context and events.
- Prefer existing project conventions and the narrowest dependency change. When behavior or
  configuration is tool-specific, document the interoperable contract rather than encoding a
  vendor-specific assumption in shared code.

## Verification and handoff

- Verification is proportional to risk: run focused tests for a small change, the relevant
  service suite for service changes, and cumulative smoke checks for milestone or cross-service
  changes. For containers or deployment changes, build/run the affected artifact and verify its
  externally visible contract when practical.
- Before calling work complete, report exact commands and outcomes, inspect the diff and status,
  scan changed files for secrets/placeholders, and record any limitation or unverified path.
- A milestone is complete only after its acceptance checks, documentation update, manual demo
  instructions, reviewable PR, and verification are done. Stop and wait for the next milestone.

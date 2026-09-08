# Next Session Handoff

Last updated: 2026-09-07 (America/Toronto)

## Start here

The AWS demo is intentionally **parked** to control cost. Read
[the runtime runbook](demo-runtime.md) before any AWS testing. The next focused
task is [issue #89](https://github.com/NakulManchanda/ai-analytics-poc/issues/89):
move Terraform state to private, versioned S3 storage and add secure,
release-driven GitHub Actions deployment.

Do not start the voice/multimodal milestone until #89 is complete. Do not resume
the paid AWS backend merely to develop or test #89.

## Current repository state

- `main` is at `654dd32937de12a763c138b821320ff2f90f416b`, the merge of
  [PR #90](https://github.com/NakulManchanda/ai-analytics-poc/pull/90).
- PR #87 added Terraform park/resume support and is merged. Issue #86 is closed.
- PR #90 added the shared application-wide Bedrock allowance and explicit local
  Bedrock mode. Issue #88 is closed by the merged PR.
- Release tags remain `v1`, `v1.1-foundation-truthful-state`, `v2`,
  `v2-streaming-text`, `v3`, and `v3-cancellable-runs`. The cost-control changes
  have not been tagged or deployed to ECS.
- The main checkout contains unrelated user changes. Preserve them.

## Verified AWS state

The authoritative local Terraform state records `demo_enabled = false`, and a
fresh plan reported no changes after parking.

- ECS has zero services and zero tasks.
- The application load balancer and ElastiCache Redis node are absent.
- `https://ai.sibkaro.com/` returns the static site with HTTP 200.
- `/api/*` returns the intentional uncached JSON HTTP 503 while parked.
- DynamoDB remains active with the durable application records retained.
- The frontend S3 bucket and both ECR repositories remain.
- Ignored local state and tfvars backups were created before the parking apply.

Full apply and verification evidence is recorded on
[issue #86](https://github.com/NakulManchanda/ai-analytics-poc/issues/86).

## Bedrock modes and allowance

Default Docker Compose and automated tests use the deterministic fake LLM. Real
local Bedrock testing is explicitly opt-in:

```sh
export DYNAMODB_TABLE_NAME=<shared-application-state-table>
export AWS_PROFILE=<local-profile>
make local-bedrock-compose
```

This starts the local app with Amazon Nova Micro and uses the shared DynamoDB
table for the UTC-month allowance. A direct local Nova Micro call was verified
after PR #90 merged: it returned `LOCAL_BEDROCK_OK` using 11 input tokens and 8
output tokens. The ledger reserved 4,498 micro-USD for that bounded call.

The allowance defaults to USD 5 per UTC month across this application's real
Bedrock calls. Each call reserves a conservative maximum before invocation.
It is not an AWS invoice cap, and failed provider calls retain their reservation.
Keep every real-Bedrock process on the same shared table and limit value.

An `/api/ask` smoke without the MCP service reached Bedrock but ended with
`mcp_tool_error`; use the Compose command above for end-to-end local testing.

## Next roadmap item: issue #89

Continue from `.worktrees/remote-tf` on branch `codex/remote-tf`. It contains
uncommitted work from an interrupted session based on PR #87. Inspect it first,
preserve useful changes, then rebase or merge current `origin/main` before
continuing. Do not discard the worktree.

Issue #89's accepted direction is:

1. Upgrade Terraform from 1.5.6 to the current stable 1.16.1 release.
2. Bootstrap a private S3 state bucket with versioning, encryption, TLS-only
   access, Block Public Access, and native S3 state locking.
3. Migrate the authoritative local state with a tested recovery path. Migration
   is a separate live action and requires review of the concrete procedure.
4. Use GitHub OIDC with no stored AWS access keys and restrict trust to this
   public repository's protected release environment.
5. Keep public PR and ordinary-merge workflows credential-free: formatting,
   validation, and tests only.
6. Deploy only from an explicit milestone tag. Generate the real plan inside a
   protected workflow, expose only sanitized add/change/destroy counts, require
   human environment approval, and apply the exact tagged revision.
7. Never publish full plans, state, tfvars, environment variables, identifiers,
   or plan artifacts in logs, comments, summaries, or downloadable artifacts.
8. Keep local Terraform usable against the same S3 state for inspection,
   parking, and emergency operation. Never run local and GitHub applies at the
   same time or disable locking.

No AWS backend bucket, OIDC role, state migration, deployment, or release tag
has been created for #89 yet.

## After issue #89

Return to the next conceptual milestone: voice/audio input, barge-in using the
existing cancellation path, and streamed speech output. Keep it as a separate
milestone with its own plan, issue tracks, PRs, tests, and explicit release/tag
decision.

## Copy-ready next-session prompt

```text
Read AGENTS.md, docs/next-session-handoff.md, docs/demo-runtime.md,
docs/progress.md, and issue #89. The AWS backend is parked and must remain
parked. PRs #87 and #90 are merged on main at 654dd32. Continue issue #89 from
the existing uncommitted .worktrees/remote-tf worktree: inspect and preserve its
work, integrate current origin/main, then finish the private S3 backend, native
locking, public-repo-safe GitHub OIDC, and milestone-tag deployment workflow.
Do not expose Terraform plans/state or apply/migrate/deploy/tag without the
required concrete review and authorization. Keep local Terraform usable against
the same remote state. Use one lightweight agent at most; avoid parallel agents
unless explicitly requested. After #89 is complete, stop and wait before the
voice/multimodal milestone.
```

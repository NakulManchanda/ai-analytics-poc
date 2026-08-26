---
name: project-pr-review
description: Use when asked to review a pull request, decide whether a PR is mergeable, start review with CI, or re-review a follow-up commit in ai-analytics-poc. Triggers include "review this PR", "is this mergeable", "start review with CI", "re-review after fixes", and "check review findings".
---

# Project Pull-Request Review

Apply this wrapper, then use Claude's built-in `review` capability for diff analysis.

## Context to load

Read `CLAUDE.md` (imports `AGENTS.md`) and `docs/agent-coordination.md`. Inspect the issue and
acceptance criteria, PR body, exclusions, base/head SHAs, diff, and test results. Read milestone
documents only when they govern the change.

## Review lane

- Use exactly one Claude session; no Gemini, Copilot, or second reviewer unless explicitly asked.
  Existing human reviews do not count as duplicate agent reviewers.
- Use Opus at normal/default effort for cross-service, architecture, security, or adversarial
  changes; use Sonnet at normal/default effort for easy localized changes. Omit `--effort`; high
  effort requires explicit direction.
- Keep the review read-only: do not edit, commit, push, merge, deploy, or apply infrastructure.
- Start final validation only after implementation/docs are complete and focused local checks pass.
  Push, then run review and GitHub Actions in parallel while the main conversation continues.

## Initial review

Review `BASE_SHA..HEAD_SHA` against the issue and repository boundaries. Report evidence-backed
findings by severity with file/line, impact, and correction. State when there are no substantive
findings. Retain the Claude session ID.

## Follow-up review

Resume that session with the new head SHA, fix summary, and prior findings. Inspect the incremental
diff for regressions. Do not start fresh or repeat the full review unless the contract changed.

## CI coordination

Use the issue, `AGENTS.md`, and existing Make targets to select the required local matrix. Add a
small Make target only when a non-trivial command will recur; do not reconstruct long commands for
one-off checks. Run focused local checks, then push. GitHub Actions is the final CI/CD test gate. Check once at
launch; if CI normally takes about 90 seconds, wait at least that long or use one bounded long watch.
Never poll at short intervals.

Combine CI and review findings. After changes, rerun affected local checks, push, await fresh CI,
and resume Claude. Results for an older SHA do not validate the new head.

## Complete PR validation

Before reporting the PR ready:

1. Match the final diff to issue acceptance and exclusions.
2. Confirm focused tests, relevant suites, format/lint, production build, and green CI on `HEAD`.
3. Confirm Claude has no unresolved substantive findings on `HEAD`.
4. Use GitHub PR state to confirm conflict-free mergeability and confirm required docs, work
   history, operational steps, and limitations are current.
5. Inspect status/diff, run `git diff --check`, and scan changed files for secrets.

Pending, failed, unexpectedly skipped, or stale evidence means not ready. Deployment is separate
unless explicitly in scope.

## Milestone or tag checkpoint

Only when the team decides to redeploy the full environment for a milestone/tag checkpoint, give
the user exact manual steps and ask for confirmation. Include the deployed sample query and
applicable AWS checks: identity, image/task revision, health, DynamoDB records/restart recovery, and
CloudWatch errors. Intermediate PRs need neither human manual testing nor separate merge approval.
Never deploy implicitly or claim verification without the required evidence.

## Merge boundary

Mechanical mergeability is not readiness. After acceptance, local checks, exact-head CI, review,
docs, and dependency gates pass, the coordinator may merge an intermediate PR without separate
human approval. Reviewers never merge. Full redeploys, infrastructure applies, milestone releases,
and tags require explicit user authority.

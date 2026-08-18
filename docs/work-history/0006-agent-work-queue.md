# Work 0006 — GitHub agent work queue

## Goal

Document one GitHub-centered coordination protocol for bounded agent work and seed the repository
queue without authorizing implementation outside the active milestone.

## Starting state

`origin/main` at `1a2986e` had Milestone 0 awaiting review, one draft Milestone 1 PR, and no
open GitHub issues.

## Decisions

- Keep `AGENTS.md` canonical and put reusable handoff text in `docs/agent-coordination.md`.
- Model and reviewer routing is advisory and scoped to coordination; GitHub labels represent
  intended agent ownership because local CLIs are not GitHub accounts.
- Queue later milestones as blocked dependencies rather than beginning them in parallel.

## Verification

- Checked all changed Markdown links and fenced command examples.
- Reviewed the diff and scanned changed files for secret-like values.
- GitHub labels, queue issues, draft PR, and Copilot review request are recorded on the PR.

## Pull request and merge

Draft PR [#5](https://github.com/NakulManchanda/ai-analytics-poc/pull/5) is open. It seeded the
`agent:*`, `status:*`, and minimal `area:*` labels plus umbrella issue #10 and queue issues
#6–#9. Do not merge without explicit authorization.

## Lessons

Dependency-gated issues preserve the project’s one-milestone-at-a-time boundary while making the
next approved work unambiguous.

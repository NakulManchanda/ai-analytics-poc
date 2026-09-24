# 0059 — Review effort and Make-first manual testing guidance

## Goal

Make the repository workflow explicit about economical independent reviews and concise manual-test handoffs that reuse the root `Makefile`.

## Starting point

- The PR-review skill selected Opus but did not set an effort level.
- Manual-testing guidance duplicated a long Compose command even though the root `Makefile` already exposed the common workflows.
- The local Docker skill allowed stopping an unrelated project when ports conflicted.

## Decisions

- Use Claude Opus with `--effort low` for independent PR review unless the user explicitly requests a higher effort.
- Begin manual-test handoffs with `cd` into the active worktree and a short ordered list of existing root Make targets.
- Never create a second Makefile or stop another task's Compose project.
- Add a root Make target only for a demonstrated recurring gap.

## Verification

- Skill frontmatter validated with the bundled skill validator.
- Referenced Make targets were checked against the root `Makefile`.
- `git diff --check` passed.

## State

Issue: [#126](https://github.com/NakulManchanda/ai-analytics-poc/issues/126)

PR: [#127](https://github.com/NakulManchanda/ai-analytics-poc/pull/127) (draft).

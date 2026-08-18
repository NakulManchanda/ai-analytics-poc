# Work 0002 — shared agent instructions

## Goal

Establish one canonical `AGENTS.md` with thin Claude Code, Gemini CLI, and GitHub Copilot
adapters, preserving the project requirements and making review/verification expectations clear.

## Starting state

Branch `chore/agent-instructions` was created in `.worktrees/agent-instructions` from fetched
`origin/main` after Milestone 0 implementation.

## Decisions

- `AGENTS.md` is canonical; `CLAUDE.md` and `GEMINI.md` import it with `@AGENTS.md`.
- Gemini CLI 0.46.0 installed documentation confirms `@file.md` imports (relative and absolute).
- Claude Code 2.1.187 recognizes project `CLAUDE.md` files; its current guidance supports the
  requested `@AGENTS.md` reference syntax.
- Copilot guidance is intentionally short and adds only review-specific checks.

## Verification

- Inspected `docs/implementation-plan.md`, `docs/progress.md`, and the requirements source from
  the main checkout.
- Checked `claude --version`/`--help` and `gemini --version`/`--help`; inspected installed Gemini
  CLI documentation and confirmed the import syntax.
- Validated adapter targets, Markdown links, and changed-file secret patterns.

## Pull request and merge

Draft PR pending; do not merge without explicit authorization.

## Lessons

Tool adapters should contain references and tool-specific review emphasis only; policy belongs in
one file so it cannot drift across agent clients.

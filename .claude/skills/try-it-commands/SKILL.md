---
name: try-it-commands
description: Use when the user says "next-5-commands", "give me commands to try this", or asks how to try out or manually exercise newly merged functionality. Produces five copy-paste commands and keeps them in a `make next-5-commands` target.
---

# Try-it commands

Goal: after a feature lands, hand the user five commands they can run to see it work, without them restating the request.

## Steps

1. Identify the functionality: the latest merged PR(s) or the issue the user names (`git log --oneline -5`, `gh pr view`). Read the work-history entry and `docs/evals.md`/README section for the real entry points; do not guess flags.
2. Choose five commands in the order a person would try them: (1) the main user path, (2) a CLI or scripted equivalent, (3) the follow-up/export step, (4) an inspection of the result, (5) the offline test target. Prefer existing `make` targets over raw commands. Use real run ids, files and ports from the repo, with `<your name>`-style placeholders only for values the user must supply. Never include secrets.
3. Verify before handing over: run the print target, and exercise every command that is safe on a scratch copy of the data (never write into real evidence or another task's services). Say which commands were not run.
4. Update the root `Makefile` target `next-5-commands` (documented with `##`, listed in `.PHONY`) so `make next-5-commands` reprints them. Replace the previous contents; keep it a pure `echo` target with no side effects. Do not create a second Makefile.
5. If a next-session file is in play (`.vscode/myfiles/next_session/<date>.md`), append the same block there.
6. Small Makefile-only changes may go straight to `main` per AGENTS.md; otherwise use the normal branch/worktree flow.

## Report

Show the five commands, which ones were run and their outcome, and what is still unverified (for example browser interaction).

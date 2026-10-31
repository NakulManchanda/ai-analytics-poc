---
name: try-it-commands
description: Use when the user says "next-5-commands", "give me commands to try this", or asks how to try out or manually exercise newly merged functionality. Produces 5-10 copy-paste commands with startup options and keeps them in a `make next-5-commands` target.
---

# Try-it commands

Goal: after a feature lands, hand the user 5-10 commands (never fewer than five) they can run to see it work, without them restating the request.

## Steps

1. Identify the functionality: the latest merged PR(s) or the issue the user names (`git log --oneline -5`, `gh pr view`). Read the work-history entry and `docs/evals.md`/README section for the real entry points; do not guess flags.
2. Open with a startup block: prerequisites (is Docker/Bedrock/a live stack needed?), required and optional variables or flags (name, default, what it changes), and how to stop. Then choose 5-10 commands in the order a person would try them: offline tests, the main user path, variants of it (other port, other run), the CLI equivalent, the follow-up/export step, inspection of the result, and any read-only fallback. Prefer existing `make` targets over raw commands. Use real run ids, files and ports from the repo, with `<your name>`-style placeholders only for values the user must supply. Never include secrets.
3. Verify before handing over: run the print target, and exercise every command that is safe on a scratch copy of the data (never write into real evidence or another task's services). Say which commands were not run.
4. Keep the target name `next-5-commands` and update the root `Makefile` target `next-5-commands` (documented with `##`, listed in `.PHONY`) so `make next-5-commands` reprints them. Replace the previous contents; keep it a pure `echo` target with no side effects. Do not create a second Makefile.
5. Also save the block in the working scratch folder (`.vscode/myfiles/<issue>-<slug>/try-it.md`) and in the next-session file if one is in use, and post it as a comment on the most recent ticket.
6. Small Makefile-only changes may go straight to `main` per AGENTS.md; otherwise use the normal branch/worktree flow.

## Report

Show the startup block and the commands, which ones were run and their outcome, and what is still unverified (for example browser interaction).

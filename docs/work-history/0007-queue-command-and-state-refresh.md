# Work 0007 — Queue command and state refresh

## Goal

Clarify the local Gemini/Antigravity command and reconcile the agent queue after the related pull
requests merged.

## Starting state

The merged coordination guide named Gemini as an instruction adapter but did not explicitly state
that local Gemini/Antigravity work is invoked with `agy`. Queue review issues #6 and #9 referred
to merged pull requests.

## Decisions

- Keep `GEMINI.md` as the instruction adapter; use `agy` as the copy-ready local command.
- Use `.worktrees/<issue-name>` from the repository root in every manual command template.
- Retarget still-relevant reviews to the merged Terraform foundation and current dataset PR, while
  closing completed review items with evidence.

## Verification

- Checked changed Markdown, work-history links, and command examples.
- Reviewed the GitHub issue state and recorded only read-only future review work.

## Pull request and merge

Draft PR pending; do not merge without explicit authorization.

## Lessons

Agent routing labels and CLI command names are different concerns; the queue needs both to make a
handoff executable.

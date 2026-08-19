# Work 0013 — Queue command and state refresh

## Goal

Clarify the local Gemini/Antigravity command and reconcile the agent queue after the related pull
requests merged.

## Starting state

The merged coordination guide named Gemini as an instruction adapter but did not explicitly state
that local Gemini/Antigravity work is invoked with `agy`. Queue review issues #6 and #9 referred
to merged pull requests.

## Decisions

- Keep `GEMINI.md` and `CLAUDE.md` as thin instruction adapters; use `agy` and `claude` as the
  copy-ready local commands.
- Use `.worktrees/<issue-name>` from the repository root in every manual command template, and
  document the authorized model/mode conventions (Claude Opus 4.8 high, Gemini 3.7 Flash High)
  with the dangerous-mode caveat that it still obeys per-issue no-merge/no-apply authorization.
- Retarget still-relevant reviews to the merged Terraform foundation and current dataset PR, while
  closing completed review items with evidence.
- Merge `origin/main` M2 work (dataset spike `0007`, MCP dataset `0008`) and renumber this
  governance entry from `0007` to the reserved sequence `0013` to preserve those merged entries.

## Verification

- Checked changed Markdown, work-history links, and command examples.
- Reviewed the GitHub issue state and recorded only read-only future review work.

## Pull request and merge

Draft PR [#13](https://github.com/NakulManchanda/ai-analytics-poc/pull/13) is open; do not merge
without explicit authorization.

## Lessons

Agent routing labels and CLI command names are different concerns; the queue needs both to make a
handoff executable.

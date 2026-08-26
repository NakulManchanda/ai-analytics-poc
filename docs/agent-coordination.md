# Agent coordination

`AGENTS.md` is the canonical policy. This guide supplies queue mechanics and copy-ready
handoffs; it does not relax milestone sequencing or duplicate project rules.

## Queue protocol

1. The coordinator creates a GitHub issue before assigning write work. One issue has one
   expected owner label, branch, project-local `.worktrees/<topic>` worktree, and draft PR.
2. The issue body names dependencies, the current milestone, a bounded acceptance checklist, and
   whether implementation is authorized. A `status:blocked` issue is planning only.
3. The owner posts issue comments for takeover, decisions, evidence, blockers, and the draft PR.
   Keep all implementation on the issue branch; never share a write worktree.
4. The coordinator controls integration order. No agent merges, applies infrastructure, or starts
   a later milestone unless the issue explicitly authorizes it. GitHub Actions and review are
   merge gates.

Ownership labels describe the intended local tool, not a GitHub identity. Assign
`NakulManchanda` when available. See the issue queue for the exact branch and worktree names.

## Model and reviewer routing

| Work | Default routing |
| --- | --- |
| Main coordinator | Codex `gpt-5.6-sol`, high reasoning |
| Normal Codex subtask | Codex `gpt-5.6-terra`, medium reasoning |
| Complex code/security review | Codex `gpt-5.6-terra`, high reasoning |
| Cross-service/architecture/security PR review | Claude Opus via `claude`, normal/default effort |
| Easy localized PR review | Claude Sonnet via `claude`, normal/default effort |
| Research, schemas, docs, test matrices | Gemini/Antigravity 3.7 Flash High via `agy`, high effort |
| Independent pull-request review | Exactly one continuing Claude session |
| Merge gate | GitHub Actions, started in parallel with review |

Do not select Luna unless the task is explicitly speed-only.

### Review lifecycle

- Push a reviewable commit, then start one Claude review in parallel with GitHub Actions. Do not
  run Claude and Gemini as duplicate PR reviewers unless the user explicitly requests both.
- Use Opus at normal/default effort for cross-service, architecture, security, or adversarial
  changes. Use Sonnet at normal/default effort for easy, localized fixes. Do not select high effort
  by default because it consumes the review budget too quickly.
- Give the initial reviewer enough context to judge the change independently: `AGENTS.md`, the
  linked issue and acceptance criteria, explicit scope exclusions, base and head SHAs, the PR URL,
  and the exact verification already run. Keep the review read-only.
- Record the Claude session ID. After addressing findings, resume that same session with the new
  head SHA, a concise summary of the fixes, and the prior findings to recheck. Do not start a fresh
  session for each follow-up review.
- Prefer one long CI wait or infrequent status snapshots. Do not repeatedly poll GitHub Actions.

## Handoff templates

Replace the bracketed values. All handoffs must begin by reading `AGENTS.md`,
`docs/implementation-plan.md`, `docs/progress.md`, and the requirements document.

### Codex

```text
Work on GitHub issue #[ISSUE]: [TITLE]. Read @AGENTS.md and docs/agent-coordination.md first.
The issue authorizes [REVIEW ONLY / IMPLEMENTATION]. Use branch [BRANCH] in
`.worktrees/<issue-name>`; do not touch another worktree. Dependencies: [DEPENDENCIES].
Acceptance: [CHECKLIST]. Post decisions, exact verification, blockers, and the draft PR URL to
the issue. Do not merge or apply anything unless this issue explicitly says so.
```

### Claude

`CLAUDE.md` already imports `@AGENTS.md`; use this focused prompt:

```text
GitHub issue #[ISSUE], [TITLE]. Follow CLAUDE.md (@AGENTS.md) and
docs/agent-coordination.md. [REVIEW ONLY / IMPLEMENTATION] on [BRANCH] in
`.worktrees/<issue-name>`. Dependencies: [DEPENDENCIES]. Acceptance: [CHECKLIST]. Leave handoff and
verification evidence on the issue. Do not merge or apply unless explicitly authorized.
```

### Gemini / Antigravity (`agy`)

`GEMINI.md` remains the instruction adapter and imports `@AGENTS.md`; invoke the local
Gemini/Antigravity CLI as `agy`:

```text
GitHub issue #[ISSUE], [TITLE]. Follow GEMINI.md (@AGENTS.md) and
docs/agent-coordination.md. [REVIEW ONLY / IMPLEMENTATION] on [BRANCH] in
`.worktrees/<issue-name>`. Dependencies: [DEPENDENCIES]. Acceptance: [CHECKLIST]. Comment evidence and
handoff on the issue. Do not merge or apply unless explicitly authorized.
```

### External CLI invocation from the assigned worktree

After the coordinator creates the assigned `.worktrees/<issue-name>` worktree, `cd` into it from
the repository root and launch the owning tool with its authorized model and mode. Run exactly one
tool per worktree, and never share a write worktree.

```sh
cd .worktrees/<issue-name>

# Claude review — Opus at normal/default effort; capture the returned session ID
claude --dangerously-skip-permissions --model opus --print --output-format json \
  "Read-only review of PR #[PR]. Read CLAUDE.md (@AGENTS.md) and docs/agent-coordination.md. \
Issue #[ISSUE]; acceptance: [CHECKLIST]; exclusions: [OUT-OF-SCOPE]; base [BASE_SHA], \
head [HEAD_SHA]; verification: [COMMANDS AND RESULTS]. Do not edit, commit, push, merge, or deploy."

# Easy localized review — use Sonnet with the same context shape
claude --dangerously-skip-permissions --model sonnet --print --output-format json \
  "Read-only review of PR #[PR]. [CONTEXT AS ABOVE]."

# Follow-up — resume the original reviewer after fixes
claude --dangerously-skip-permissions --resume "[CLAUDE_SESSION_ID]" \
  --print --output-format json \
  "Re-review new head [NEW_HEAD_SHA]. Fixes since the prior review: [SUMMARY]. \
Recheck every prior finding and inspect the incremental diff for regressions. Remain read-only."

# Gemini / Antigravity — 3.7 Flash High, accept-edits
agy --dangerously-skip-permissions --mode=accept-edits --effort=high \
  --model=gemini-3.7-flash-high \
  "GitHub issue #[ISSUE]: read GEMINI.md (@AGENTS.md) and docs/agent-coordination.md; [TASK]."
```

The task text must include the authorization level, dependencies, branch/worktree, acceptance
checklist, and the instruction to comment evidence on the issue.

#### Authorized model and mode conventions

| Tool | Model | Effort | Mode flag |
| --- | --- | --- | --- |
| `claude` | `opus` for complex reviews; `sonnet` for easy localized reviews | normal/default (omit `--effort`) | `--dangerously-skip-permissions` |
| `agy` | `gemini-3.7-flash-high` (Gemini 3.7 Flash High) | `--effort=high` | `--dangerously-skip-permissions --mode=accept-edits` |

`--dangerously-skip-permissions` is authorized **only** inside the isolated `.worktrees/<issue-name>`
worktree the coordinator assigned to that tool. It removes local approval prompts; it does **not**
grant merge or apply authority. Every run still obeys the issue's explicit no-merge/no-apply
authorization — do not merge, push to `main`, or apply infrastructure unless the issue says so.

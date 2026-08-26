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
4. The coordinator controls integration order. A gated intermediate PR needs no separate human
   merge approval. Reviewers never merge, and no agent applies infrastructure, redeploys the full
   environment, creates a milestone tag, or starts a later milestone without explicit authority.
   GitHub Actions and one independent review are merge gates.

Ownership labels describe the intended local tool, not a GitHub identity. Assign
`NakulManchanda` when available. See the issue queue for the exact branch and worktree names.

## Model and reviewer routing

| Work | Default routing |
| --- | --- |
| Main coordinator | Codex `gpt-5.6-sol`, high reasoning |
| Normal Codex subtask | Codex `gpt-5.6-terra`, medium reasoning |
| Complex code/security review | Codex `gpt-5.6-terra`, high reasoning |
| Research, schemas, docs, test matrices | Gemini/Antigravity 3.7 Flash High via `agy`, high effort |
| Independent pull-request review and validation | `.claude/skills/project-pr-review/SKILL.md` |

Do not select Luna unless the task is explicitly speed-only.

### Review lifecycle

Requests such as “review this PR,” “validate the PR,” “is this mergeable?”, “start review with CI,”
or “re-review after fixes” trigger `.claude/skills/project-pr-review/SKILL.md`. Use it as the sole
source for review and validation mechanics.

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

# Claude review and follow-up commands are selected by the project PR-review skill.
# Ask naturally: "Review PR #[PR] for issue #[ISSUE]. Is it mergeable?"

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
| `agy` | `gemini-3.7-flash-high` (Gemini 3.7 Flash High) | `--effort=high` | `--dangerously-skip-permissions --mode=accept-edits` |

`--dangerously-skip-permissions` is authorized **only** inside the isolated `.worktrees/<issue-name>`
worktree the coordinator assigned to that tool. It removes local approval prompts; it does **not**
grant merge or apply authority. Every run still obeys the issue's explicit no-merge/no-apply
authorization — do not merge, push to `main`, or apply infrastructure unless the issue says so.

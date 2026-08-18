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
| Architecture/security/adversarial review | Claude Opus, high reasoning |
| Research, schemas, docs, test matrices | Gemini/Antigravity Flash, high reasoning |
| Pull-request review | GitHub Copilot |
| Merge gate | GitHub Actions |

Do not select Luna unless the task is explicitly speed-only.

## Handoff templates

Replace the bracketed values. All handoffs must begin by reading `AGENTS.md`,
`docs/implementation-plan.md`, `docs/progress.md`, and the requirements document.

### Codex

```text
Work on GitHub issue #[ISSUE]: [TITLE]. Read @AGENTS.md and docs/agent-coordination.md first.
The issue authorizes [REVIEW ONLY / IMPLEMENTATION]. Use branch [BRANCH] in
.worktrees/[TOPIC]; do not touch another worktree. Dependencies: [DEPENDENCIES].
Acceptance: [CHECKLIST]. Post decisions, exact verification, blockers, and the draft PR URL to
the issue. Do not merge or apply anything unless this issue explicitly says so.
```

### Claude

`CLAUDE.md` already imports `@AGENTS.md`; use this focused prompt:

```text
GitHub issue #[ISSUE], [TITLE]. Follow CLAUDE.md (@AGENTS.md) and
docs/agent-coordination.md. [REVIEW ONLY / IMPLEMENTATION] on [BRANCH] in
.worktrees/[TOPIC]. Dependencies: [DEPENDENCIES]. Acceptance: [CHECKLIST]. Leave handoff and
verification evidence on the issue. Do not merge or apply unless explicitly authorized.
```

### Gemini

`GEMINI.md` already imports `@AGENTS.md`; use this focused prompt:

```text
GitHub issue #[ISSUE], [TITLE]. Follow GEMINI.md (@AGENTS.md) and
docs/agent-coordination.md. [REVIEW ONLY / IMPLEMENTATION] on [BRANCH] in
.worktrees/[TOPIC]. Dependencies: [DEPENDENCIES]. Acceptance: [CHECKLIST]. Comment evidence and
handoff on the issue. Do not merge or apply unless explicitly authorized.
```

### Manual CLI invocation

Run from the assigned worktree after the coordinator has created it:

```sh
cd /Users/nakulmanchanda/dev/ai_app_poc/.worktrees/[TOPIC]
agy "GitHub issue #[ISSUE]: read @AGENTS.md and docs/agent-coordination.md; [TASK]."
claude "GitHub issue #[ISSUE]: follow CLAUDE.md (@AGENTS.md) and docs/agent-coordination.md; [TASK]."
```

The task text must include the authorization level, dependencies, branch/worktree, acceptance
checklist, and the instruction to comment evidence on the issue.

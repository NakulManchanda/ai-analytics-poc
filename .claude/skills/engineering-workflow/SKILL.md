---
name: engineering-workflow
description: Entry point for repository-changing engineering work in ai-analytics-poc. Keeps the workflow short by routing through three focused phases: intent/plan, build/verify, and review/finish.
---

# Engineering Workflow

`AGENTS.md` is canonical. This skill is only the traffic controller.

## Rule: never hold the whole workflow in your head

Work in exactly three phases. Finish the current phase artifact before moving to the next.

```text
PHASE 1 — INTENT + PLAN
        ↓
PHASE 2 — BUILD + VERIFY
        ↓
PHASE 3 — REVIEW + FINISH
```

At any point the user may override, skip, redirect, or accept risk. Record material overrides in the issue or `.vscode/myfiles/<issue>-<slug>/decisions.md`.

## Setup

For repository-changing work:

1. Use one GitHub issue, one branch, and one project-local `.worktrees/<issue>-<slug>` worktree whenever a local checkout is available. Keep branch names tool-agnostic and descriptive (e.g. `feat/<issue>-<slug>`); do not hardcode model/tool prefixes like `codex/`. Tag or note the acting agent/model in the PR description.
2. Treat the primary checkout as read-only unless worktrees are unavailable or the user explicitly overrides.
3. Create ignored scratch state under `.vscode/myfiles/<issue>-<slug>/`.

Suggested scratch files:

```text
intent.md
plan.md
decisions.md
experiments/
delegation/
diff-story.md
review/
```

## Routing

- Start with `phase-1-intent-plan`.
- After its exit gate is satisfied, run `phase-2-build-verify`.
- After implementation and verification are complete, run `phase-3-review-finish`.
- Do not jump ahead because later phases depend on artifacts from earlier phases.

## Cost policy

Use expensive reasoning for decisions, not mechanical work.

- Orchestrator: frontier reasoning model for ambiguity, architecture, integration, risk, and escalation.
- Worker: lowest-cost capable model for bounded tasks with explicit acceptance criteria.
- Reviewer: fresh, read-only, adversarial frontier model independent from the writer.

Escalate from worker to orchestrator when scope expands, architecture/security boundaries change, multiple services interact unexpectedly, or the worker is uncertain.

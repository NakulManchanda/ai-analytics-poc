---
name: phase-2-build-verify
description: Phase 2 of the engineering workflow. Use after intent and plan are ready. Owns bounded delegation, implementation with tests/tooling, and local/manual verification.
---

# Phase 2 — Build + Verify

This phase owns only three things:

```text
1. DELEGATE OR IMPLEMENT
2. TEST
3. VERIFY
```

Read `intent.md` and `plan.md` first. Do not redesign the task unless new evidence invalidates the plan.

## 1. Delegate or implement

Use the lowest-cost capable worker for bounded tasks.

A worker handoff must include:

```text
Context
Task
Allowed files
Do
Do not
Acceptance criteria
Verification
Escalate instead of guessing if...
```

Good low-cost worker tasks:

- small experiments;
- localized implementation;
- unit-test generation;
- mechanical refactors;
- documentation;
- schema/API inspection;
- simple Make targets.

Keep the frontier orchestrator on architecture, security, cross-service integration, ambiguous requirements, and conflict resolution.

Implement in small vertical slices:

```text
small change
  -> focused test
  -> inspect result
  -> continue
```

## 2. Test

Every meaningful behavior should normally have unit tests.

Prefer existing Make targets. Add a small documented Make target when a non-trivial command will recur; do not wrap one-off inspection commands.

## 3. Verify

Run focused local verification proportional to risk.

When the user asks for manual testing, or a live local stack is the right acceptance path, delegate to the existing `local-docker-test` skill rather than duplicating Docker instructions here.

Record exact commands, outcomes, and any unverified path.

## Exit gate

Phase 2 is done when:

- planned code/docs/tooling are implemented;
- focused tests pass;
- recurring developer commands are easy to run;
- required manual verification is complete or explicitly waived;
- no known implementation blocker remains.

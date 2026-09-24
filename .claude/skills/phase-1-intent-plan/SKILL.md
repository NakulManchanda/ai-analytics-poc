---
name: phase-1-intent-plan
description: Phase 1 of the engineering workflow. Use to clarify intent, resolve important uncertainty, and produce a bounded implementation plan before code changes.
---

# Phase 1 — Intent + Plan

This phase owns only three things:

```text
1. CLARIFY
2. PROVE UNCERTAINTY
3. PLAN
```

Do not implement production code in this phase.

## 1. Clarify

Create `myfiles/<issue>-<slug>/intent.md` with:

- goal and why it matters;
- current vs desired behavior;
- in scope / out of scope;
- constraints and invariants;
- acceptance criteria;
- assumptions, risks, and unknowns.

Interview the user only for high-impact ambiguity. Do not chase 100% confidence.

Classify unknowns as:

- **BLOCKING** — ask or investigate before continuing;
- **TESTABLE** — run a tiny experiment;
- **LOW-RISK ASSUMPTION** — record and continue;
- **USER OVERRIDE** — record the decision and continue.

## 2. Prove important uncertainty

For TESTABLE unknowns, create the smallest possible experiment under:

`myfiles/<issue>-<slug>/experiments/`

Prefer flat, hard-coded, disposable scripts that answer one question. Do not prematurely abstract experimental code into production code.

Record:

```text
Question
Experiment
Observed result
Decision
```

## 3. Plan

Create `plan.md` as an ordered implementation story.

Each step must say:

- what changes;
- why it changes;
- how it will be verified.

Keep the plan small enough that Phase 2 can execute it without rediscovering intent.

## Exit gate

Phase 1 is done when:

- acceptance criteria are explicit;
- no high-impact unknown is silently unresolved;
- the implementation sequence is clear;
- the user can override and move forward at any time.

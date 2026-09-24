---
name: phase-3-review-finish
description: Phase 3 of the engineering workflow. Use after implementation and verification. Owns diff storytelling, adversarial independent review, CI, and readiness handoff.
---

# Phase 3 — Review + Finish

This phase owns only three things:

```text
1. EXPLAIN THE DIFF
2. TRY TO BREAK IT
3. PROVE HEAD IS READY
```

No new feature work should begin here. New substantive scope returns to Phase 1.

## 1. Explain the diff

Create `myfiles/<issue>-<slug>/diff-story.md`.

Explain changed files in dependency/reading order:

```text
file
  -> purpose
  -> behavior introduced/changed
  -> why it comes before the next file
```

The result should read like an implementation story rather than a file inventory.

Use it to refresh the PR description with context, decisions, verification, and limitations.

## 2. Try to break it

Use the existing `project-pr-review` skill for final validation.

The final reviewer must be:

- a fresh session;
- read-only;
- independent from the writer;
- an adversarial frontier model appropriate for the change.

The review should actively look for violated requirements, incorrect assumptions, race/retry/idempotency bugs, security-boundary mistakes, failure-handling gaps, backwards-compatibility problems, state corruption, observability blind spots, weak negative tests, and unnecessary operational complexity.

Do not spend frontier review cost on mechanical implementation when a lower-cost worker was sufficient; intentionally spend it here.

## 3. Prove HEAD is ready

Combine:

- issue acceptance;
- local/focused verification;
- independent review findings;
- exact-head GitHub Actions;
- conflict/mergeability state;
- docs/work-history status;
- secret/diff hygiene.

Older CI or review evidence does not validate a newer HEAD.

## Exit gate

Report READY only when the existing `project-pr-review` readiness gates pass. Reviewers never merge. Deploy/apply/tag/release boundaries remain governed by `AGENTS.md`.

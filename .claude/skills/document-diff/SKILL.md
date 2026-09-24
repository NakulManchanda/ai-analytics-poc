---
name: document-diff
description: Use near the end of implementation to document only what the current diff reasonably requires. Produces useful module/file context, work history, PR/diff story, and affected docs without adding boilerplate or changing runtime behavior.
---

# Document the Diff

This is a bounded documentation pass over the current issue + final implementation diff.

## Goal

Make the change understandable to the next engineer without turning documentation into ceremony.

Use a lower-cost capable documentation agent when possible. The documentation agent must not change runtime behavior.

## Inputs

Read:

- the GitHub issue and acceptance criteria;
- `intent.md` and `plan.md` when present;
- the final diff;
- existing nearby documentation conventions.

Document only behavior supported by the code and verification evidence. Do not invent future architecture.

## Reasonable standards

Apply judgment; not every standard applies to every diff.

### 1. Meaningful source-file header

For a new or substantially changed non-obvious source module, add a concise module/file docstring or header when it provides real orientation.

Good headers answer:

- What responsibility does this file own?
- What important boundary or invariant does it enforce?
- What clearly belongs elsewhere?

Keep it short. Skip obvious files, generated files, configuration whose purpose is self-evident, and tiny modules where the code is clearer than a header.

Do not add headers that merely repeat the filename.

### 2. Comments explain why, not what

Add comments only for things such as:

- non-obvious invariants;
- protocol or provider constraints;
- compatibility workarounds;
- surprising failure/retry behavior;
- intentional trade-offs.

Do not narrate straightforward code line by line.

### 3. Public API documentation when needed

Document public functions/classes/interfaces when their contract, side effects, failure behavior, or constraints are not obvious from names and types.

Do not mechanically add docstrings to every function.

### 4. Work history

Every PR keeps the repository's work-history habit.

Capture:

- goal;
- starting point;
- important decisions;
- verification;
- limitations / lessons;
- issue / PR state.

Keep it factual and useful for future archaeology.

### 5. README / operator docs only when behavior changes

Update README, setup, runbook, troubleshooting, or developer workflow docs only when the diff changes something a user/operator/developer needs to know.

Examples:

- new command;
- new setup requirement;
- new operational path;
- changed configuration;
- changed externally visible behavior.

### 6. Architecture docs only for architecture changes

Use Mermaid or update architecture docs when component relationships, request/data flow, or ownership boundaries materially change.

Create/update an ADR only for a durable architectural decision with a meaningful trade-off. Ordinary implementation choices do not need ADRs.

### 7. Diff story / PR description

Create or refresh `myfiles/<issue>-<slug>/diff-story.md` and the PR description.

Explain changed files in logical dependency order:

```text
file
  -> responsibility
  -> why it changed
  -> behavior/evidence
```

Prefer an implementation story over an alphabetical file list.

### 8. Documentation must stay truthful

Before finishing, check that docs:

- describe implemented behavior, not intent that was later abandoned;
- match current names, commands, paths, and configuration;
- do not claim tests/manual verification that did not run;
- do not expose secrets or developer-specific absolute paths.

## Do not go overboard

Do not:

- add comments/docstrings only to increase coverage;
- rewrite unrelated docs for style;
- create an ADR for routine code choices;
- create diagrams for a local implementation detail;
- duplicate the same explanation across README, source comments, work history, and PR body;
- change production logic during this pass.

If no documentation change is warranted for a category, explicitly skip it.

## Output

Return a compact summary:

```text
Updated:
- ...

Skipped as unnecessary:
- ...

Evidence checked:
- ...
```

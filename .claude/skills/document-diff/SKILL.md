---
name: document-diff
description: Use near the end of implementation to document only what the current diff reasonably requires. Keeps source context, work history, affected docs, and the PR story useful without adding boilerplate or changing runtime behavior.
---

# Document the Diff

Run this as a bounded, lower-cost documentation pass over the issue + final diff.

The documentation agent must not change runtime behavior and must document only behavior supported by code and verification evidence.

## Read first

- issue + acceptance criteria;
- `intent.md` / `plan.md` when present;
- final diff;
- nearby repository documentation conventions.

## Reasonable standards

Apply only what helps.

### 1. File/module context

For a new or substantially changed **non-obvious source module**, add a short top-level docstring/header when useful.

It should answer:

- what responsibility this file owns;
- an important boundary/invariant;
- what clearly belongs elsewhere, if that prevents confusion.

Skip obvious, tiny, generated, and self-explanatory files. Never add a header that only repeats the filename.

### 2. Comments explain why

Comment non-obvious invariants, protocol/provider constraints, compatibility workarounds, surprising failure behavior, or deliberate trade-offs.

Do not narrate straightforward code.

### 3. Public contracts

Document public APIs when side effects, failure behavior, constraints, or semantics are not obvious from names/types.

Do not mechanically docstring every function.

### 4. Work history

Every PR keeps the repository work-history habit:

- goal;
- starting point;
- important decisions;
- verification;
- limitations / lessons;
- issue / PR state.

### 5. Update affected docs only

Update README/setup/runbook/troubleshooting docs only when the diff changes something a developer, user, or operator must know: commands, setup, configuration, operational steps, or externally visible behavior.

### 6. Architecture threshold

Update Mermaid/architecture docs only when component relationships, flow, or ownership boundaries materially change.

Create/update an ADR only for a durable architectural decision with a meaningful trade-off.

### 7. Diff story

Create or refresh `.vscode/myfiles/<issue>-<slug>/diff-story.md` and the PR description in logical dependency order:

```text
file
  -> responsibility
  -> why it changed
  -> behavior / evidence
```

Prefer a coherent implementation story over an alphabetical file list.

## Truthfulness check

Before finishing, confirm docs:

- match current names, commands, paths, and configuration;
- describe implemented behavior rather than abandoned intent;
- do not claim tests/manual verification that did not run;
- contain no secrets or developer-specific absolute paths.

## Avoid documentation debt disguised as documentation

Do not:

- add comments/docstrings for quota;
- rewrite unrelated docs for style;
- duplicate the same explanation everywhere;
- create diagrams or ADRs for local implementation details;
- change production logic during this pass.

## Output

Report only:

```text
Updated:
- ...

Skipped as unnecessary:
- ...

Evidence checked:
- ...
```

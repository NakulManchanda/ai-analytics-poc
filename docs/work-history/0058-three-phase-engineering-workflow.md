# 0058 — Three-phase AI-assisted engineering workflow skills

## Goal

Add a connected engineering workflow that keeps repository-changing AI work easy to resume and hard to lose in the middle. The workflow is deliberately split into three short phases: intent/plan, build/verify, and review/finish.

## Starting point

The repository already had canonical `AGENTS.md`, isolated issue/branch/worktree rules, a `local-docker-test` skill, and a `project-pr-review` skill. The missing piece was a small connected workflow that linked those capabilities without duplicating their procedures.

## Decisions

- Keep `AGENTS.md` canonical and make `engineering-workflow` a thin traffic controller.
- Use only three phase skills, each with three responsibilities and an explicit exit gate, rather than many tiny skills or one giant skill.
- Prefer a dedicated `.worktrees/<issue>-<slug>` worktree for repository-changing local work; keep the primary checkout read-only unless unavailable or explicitly overridden.
- Store temporary AI workbench artifacts under ignored `myfiles/<issue>-<slug>/`.
- Spend frontier reasoning on intent, architecture, integration, ambiguity, and final review; delegate bounded/mechanical tasks to the lowest-cost capable worker.
- Make final PR validation deliberately adversarial, fresh, read-only, and frontier-model.
- Reuse `local-docker-test` and `project-pr-review` rather than copying their procedures into the new phases.
- Add a bounded `document-diff` skill so documentation is delegated to a lower-cost worker using reasonable standards without requiring boilerplate everywhere.

## Workflow

```text
Phase 1: Intent + Plan
  clarify -> prove uncertainty -> plan

Phase 2: Build + Verify
  delegate/implement -> test -> verify

Phase 3: Review + Finish
  explain diff -> adversarial review -> exact-head readiness
```

Human override is allowed at every phase; material overrides and accepted risks are recorded.

## Files changed

- `.claude/skills/engineering-workflow/SKILL.md` — main three-phase router.
- `.claude/skills/phase-1-intent-plan/SKILL.md` — intent, experiments, and plan.
- `.claude/skills/phase-2-build-verify/SKILL.md` — cost-aware delegation, implementation, tests, and manual verification.
- `.claude/skills/phase-3-review-finish/SKILL.md` — routes final documentation, adversarial review, and exact-head readiness.
- `.claude/skills/document-diff/SKILL.md` — bounded documentation standards for file/module context, work history, affected docs, architecture thresholds, and the diff story.
- `.claude/skills/project-pr-review/SKILL.md` — strengthen final validation to use an independent adversarial frontier reviewer.
- `AGENTS.md` — route new implementation work into the connected workflow and codify model economics.
- `docs/agent-coordination.md` — document lowest-cost-capable workers and frontier final review.
- `.gitignore` — ignore `myfiles/` scratch state.

## Verification

This is a repository-policy/documentation change. Verification consists of checking the branch diff against Issue #124 acceptance criteria, validating skill cross-references and paths, and relying on exact-head GitHub Actions plus the independent PR-review skill as final gates.

No deployment, infrastructure apply, release, tag, or merge is part of this change.

## Issue / PR

- Issue: #124
- Branch: `ai124-engineering-workflow`
- PR: to be opened as draft.

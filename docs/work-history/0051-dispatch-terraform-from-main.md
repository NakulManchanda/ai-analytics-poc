# 0051 — Dispatch Terraform release from main on GitHub Actions

## Goal

Provide a convenient, one-command Make target to trigger protected Terraform releases on GitHub Actions directly from `main` or an approved tag using the `gh` CLI, and update the next session handoff for Milestone v4.

## Starting point

Following the completion of Issue #89 (PR #97), Terraform state was moved to S3 with native locking, and `.github/workflows/terraform-release.yml` was established with OIDC authentication. However, the workflow strictly enforced semver `v*` tags, preventing automated dispatches from `main`, and lacked a Makefile entry for triggering and watching runs via `gh`.

## Decisions

- **Support `main` in Release Workflow**: Updated `.github/workflows/terraform-release.yml` to accept `main` in addition to approved `v*` semver tags, verifying that `main` matches the remote `origin/main` exact HEAD.
- **Root Makefile Target**: Added `make tf-dispatch` (with optional `REF=main` and `DEMO=true/false`) which triggers `gh workflow run terraform-release.yml --ref main` and automatically launches `gh run watch` to follow live progress.
- **Terraform Directory Target**: Added `make -C infra/terraform dispatch` matching the root invocation pattern.
- **Roadmap Handoff Refresh**: Updated `docs/next-session-handoff.md` with complete post-PR #97 state (remote S3 bucket, DynamoDB GSI active, GitHub OIDC role configured, and ready for Milestone v4: Voice Input & Streaming Audio).

## Verification and status

- `uv run --project services/app pytest tests` passed (25/25 tests).
- `make -C infra/terraform fmt-check` passed.
- `git diff --check` passed.

## Lesson

Providing a developer-friendly CLI wrapper around GitHub Actions dispatch bridges the gap between local terminal developer workflows and audited cloud-native CI/CD deployments.

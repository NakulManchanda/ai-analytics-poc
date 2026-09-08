# Work 0047 — Remote Terraform state and approved release workflow

## Goal

Move Terraform toward private S3 remote state and add a release-only,
human-approved GitHub Actions apply path without performing AWS operations.

## Starting point

Issue #89 began from `origin/main` at `95953d4`, with operator-held local state
and public backendless Terraform validation.

## Decisions

- Require Terraform 1.16.1 or later and native S3 `use_lockfile`.
- Isolate state-bucket bootstrap from the main configuration. It is versioned,
  encrypted, private, TLS-only, Block-Public-Access protected, and cannot be
  destroyed through Terraform.
- Keep populated backend configuration, state, tfvars, plans, and identifiers
  outside version control. The migration procedure includes private backup,
  object-version rollback, drift checks, and backendless emergency recovery.
- Keep public PR and merge validation backendless and credential-free.
- Require an exact `v*` tag, `demo_enabled=true`, protected environment
  approval, and repository/environment/workflow-restricted OIDC to apply. Plan
  and apply stay in one job, which emits only operation counts.
- Add Global Secondary Index `entity_type-started_at-index` on the DynamoDB
  `application_state` table per ADR 0007 for telemetry and run metrics queries.


## Verification

- Focused static tests cover locking, bucket controls, release secrecy, and
  public workflow isolation.
- Terraform formatting and backendless validation run without AWS access.
- No state migration, bucket creation, AWS apply, deployment, tag, or merge
  was performed.

## Pull request and merge

Draft PR pending. Final authorized migration sequence: bootstrap the bucket,
migrate state privately, configure protected GitHub environment and a reviewed
least-privilege role policy, validate remote drift/plan privately, then dispatch
the protected workflow for an approved tag only when a demo is authorized.

## Lessons

For a public repository, a plan artifact is a data-exposure mechanism. A single
environment-approved plan/apply job preserves exact-plan semantics without
exporting sensitive Terraform data.

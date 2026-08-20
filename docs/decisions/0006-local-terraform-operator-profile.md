# ADR 0006 — Allow the existing local AWS profile for Terraform operations

## Status

Accepted for the single-user POC.

## Context

The originally anticipated local workflow required a named AWS IAM Identity
Center SSO profile. The developer has verified that the existing local default
AWS CLI profile can access the intended POC account (`<aws-account-id>`). Requiring
a second local profile would add setup without improving the POC's runtime IAM
boundary.

## Decision

Terraform may use the already-authorized local default AWS profile. The
operator verifies the selected account with `aws sts get-caller-identity`
before any AWS-dependent Terraform command. No profile name is committed or
exported by project tooling.

AWS SSO remains an optional future hardening choice for this local operator
workflow. It is not an acceptance blocker for the single-user POC.

This decision applies only to a human's local Terraform session. Deployed
application containers continue to use the dedicated ECS task roles. The
execution role remains separate from the ai-app and analytics-MCP task roles.

## Consequences

- No AWS access keys, credentials, session tokens, or CLI configuration may be
  committed to the repository.
- Credentials must not be added to environment files, Terraform variables,
  GitHub Actions, Docker images, task definitions, or containers.
- Terraform plans and applies remain operator-run steps; CI performs only
  backendless initialization, formatting, and static validation.
- If the POC gains more operators or automated deployment, revisit SSO and
  remote-state access controls before extending this workflow.

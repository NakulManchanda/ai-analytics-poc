# Work 0003 — Milestone 13 Terraform foundation

## Goal

Create the smallest reviewable AWS Terraform foundation without deploying the
application or changing AWS resources from this pull request.

## Starting state

The branch `feat/m13-terraform-foundation` was created in the isolated
`/Users/nakulmanchanda/dev/wt-m13-terraform-foundation` worktree from
`origin/main` at `a44351e`.

## Decisions

- Keep Terraform flat under `infra/terraform` and use a local-state workflow.
- Create only foundation resources: VPC/subnets/security group, ECR, private
  S3 buckets, one on-demand application-state DynamoDB table, separate ECS
  roles, ECS cluster, and CloudWatch log groups.
- Exclude ALB, CloudFront, ECS task definitions/services, Service Connect,
  images, frontend upload, and any apply from this milestone branch.
- A single local Terraform operator may use the verified default AWS profile;
  SSO is optional future hardening. Deployed containers still use ECS task
  roles, and no credentials belong in the repository or runtime configuration.
- The requirements document is now tracked as the canonical project source;
  its earlier ignored-local-input treatment ended with the bootstrap phase.

## Verification

- `terraform fmt`: passed
- `make -C infra/terraform init-backendless`: passed
- `make -C infra/terraform fmt-check`: passed
- `make -C infra/terraform validate`: passed
- `make -C infra/terraform plan TFVARS=terraform.tfvars.example`: passed
  against account `107207236011` (32 resources to add; no apply run)
- Negative input checks: one AZ and an invalid public subnet CIDR both fail
  Terraform variable validation before planning resources.
- GitHub Actions `Terraform validation / Terraform static validation`: passed
- No apply was run by this branch.

## Pull request and merge

Draft PR [#3](https://github.com/NakulManchanda/ai-analytics-poc/pull/3).
Awaiting review, credentialed plan review, and merge.

## Lessons

Backendless Terraform validation gives repeatable configuration feedback
without exposing AWS credentials to CI.

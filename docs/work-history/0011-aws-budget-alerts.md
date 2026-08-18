# Work 0011 — Optional Terraform AWS budget alerts

## Goal

Provide an optional AWS Budgets layer in Terraform for account `107207236011` with monthly
cost thresholds at $5 actual, $8 actual, and $10 actual/forecasted spend alerting a configurable
email destination, without modifying application-side runtime admission gates.

## Starting state

Milestone 13 Terraform foundation under `infra/terraform` defined VPC, subnets, ECR, S3,
DynamoDB, ECS cluster, IAM roles, and CloudWatch log groups, but did not define AWS Budgets
resources or budget alert variables. Root `terraform.tfvars.example` diverged from
`infra/terraform/terraform.tfvars.example`.

## Decisions

- Implement AWS Budgets via `aws_budgets_budget` in a dedicated `infra/terraform/budget.tf`.
- Keep budget creation optional via `enable_budget_alerts` (default `false`) and configurable
  email `budget_alert_email` (default/example `nakul1986@gmail.com`).
- Enforce AWS account `107207236011` via `account_id` and lifecycle precondition.
- Map the requirements-intent thresholds ($5 actual, $8 actual, $10 actual/forecast for a $10.00
  monthly limit) using valid AWS Budgets percentage semantics:
  - 50% actual spend ($5.00)
  - 80% actual spend ($8.00)
  - 100% actual spend ($10.00)
  - 100% forecasted spend ($10.00)
- Replace divergent root `terraform.tfvars.example` with a pointer to `infra/terraform/terraform.tfvars.example`.
- Keep the application-side $7.50 DynamoDB admission gate strictly out of scope.

## Verification

- `make -C infra/terraform init-backendless`: passed
- `make -C infra/terraform fmt-check`: passed
- `terraform fmt -check -recursive`: passed
- `make -C infra/terraform validate`: passed
- Static checks and assertions: verified variable validation, budget definitions, and non-divergent tfvars pointer
- `git diff --check`: clean
- Secret scan: verified no secrets or credentials committed

## Pull request and merge

Draft PR: Pending
Merge status: awaiting review. Do not apply and do not merge without explicit authorization.

## Lessons

Using valid AWS Budgets percentage notification blocks (50% actual, 80% actual, 100% actual,
100% forecasted) provides a precise, native representation of multi-tier cost alerting for
the $10 monthly budget cap.

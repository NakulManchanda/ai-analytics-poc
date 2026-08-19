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
  email `budget_alert_email` (default `null`, example placeholder `alert-recipient@example.com`).
  Real recipient email is provided via ignored local `terraform.tfvars` or `TF_VAR_budget_alert_email`.
- Enforce AWS account `107207236011` via `account_id` and lifecycle precondition.
- Map the requirements-intent thresholds ($5 actual, $8 actual, $10 actual/forecast for a $10.00
  monthly limit) using valid AWS Budgets `ABSOLUTE_VALUE` semantics to prevent dollar drift if
  `monthly_budget_limit_usd` is modified:
  - $5 actual spend (`GREATER_THAN` $5.00 actual)
  - $8 actual spend (`GREATER_THAN` $8.00 actual)
  - $10 actual spend (`GREATER_THAN` $10.00 actual)
  - $10 forecasted spend (`GREATER_THAN` $10.00 forecasted)
- Note operational behavior: forecasted alerts require AWS Cost Management historical account telemetry
  and may not trigger early on new accounts; actual spend alerts trigger as usage accumulates.
- Replace divergent root `terraform.tfvars.example` with a pointer to `infra/terraform/terraform.tfvars.example`.
- Strengthen test coverage with a dedicated parser-backed test suite in `tests/infra/test_budget.py`
  placed cleanly outside the application service, validating exact 4-block count and attribute pairings.
- Add numeric positive validation (`can(tonumber(var.monthly_budget_limit_usd)) && tonumber(var.monthly_budget_limit_usd) > 0`)
  with safe evaluation semantics to reject zero-valued (`0`, `00`, `0.00`) or non-positive inputs, along with a focused test.
- Integrate `origin/main` at `219608b` (Milestone 2 FastMCP dataset profile merge) cleanly into `feat/aws-budget-alerts`.
- Keep the application-side $7.50 DynamoDB admission gate strictly out of scope.

## Verification

- `make -C infra/terraform init-backendless`: passed
- `make -C infra/terraform fmt-check`: passed
- `terraform fmt -check -recursive`: passed
- `make -C infra/terraform validate`: passed
- `uv run --project services/app black --check services/app tests`: passed
- `uv run --project services/mcp black --check services/mcp/mcp_server services/mcp/tests`: passed
- `uv run --project services/dataset_spike black --check services/dataset_spike`: passed
- `uv run --project services/app ruff check services/app tests`: passed
- `uv run --project services/mcp ruff check services/mcp/mcp_server services/mcp/tests`: passed
- `uv run --project services/dataset_spike ruff check services/dataset_spike`: passed
- `make test`: all 15 tests passed across `services/app`, `services/mcp`, `services/dataset_spike`, and `tests/infra`
- `git diff --check`: clean
- Secret scan: verified no secrets or credentials committed; no personal email committed

## Pull request and merge

Draft PR: [#20](https://github.com/NakulManchanda/ai-analytics-poc/pull/20)
Merge status: awaiting review. Do not apply and do not merge without explicit authorization.

## Lessons

1. Using `threshold_type = "ABSOLUTE_VALUE"` guarantees that fixed dollar requirements ($5/$8/$10)
   remain intact regardless of adjustments to the overall monthly limit variable.
2. Forecast-based budget notifications depend on historical account usage data, so tests and
   operational documentation should highlight that forecast alerts accumulate telemetry over time.
3. Placing Terraform static assertions in `tests/infra/` rather than inside `services/app/tests/`
   maintains clean service boundaries and avoids coupling infrastructure testing to application logic.
4. Input validation for string-typed monetary variables must combine regex syntax checks with numerical
   positive checks (`tonumber(...) > 0`) to prevent zero or negative values.


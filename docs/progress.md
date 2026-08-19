# Current milestone

Milestone 13 (Optional Addition) — AWS budget alerts configuration

## Status

IMPLEMENTED — awaiting review and merge (PR #20)

## Merged milestone baseline

- **Milestone 0**: Minimal FastAPI `GET /health` endpoint (merged).
- **Milestone 1**: Independently runnable empty FastMCP service on port 8001 (merged).
- **Milestone 2**: NYC TLC Parquet dataset profile via DuckDB and FastMCP tools/resources (merged, PR #17).
- **Milestone 3**: Minimal React status shell with same-origin `/api/` proxy and Compose smoke (merged, PR #19).
- **Milestone 13 Foundation**: Terraform infrastructure foundation under `infra/terraform/` defining VPC, subnets, ECR, S3, DynamoDB, ECS, IAM, and CloudWatch log groups (merged, PR #3 / PR #11; unapplied offline configuration).

## Acceptance criteria (PR #20 — Budget Alerts)

- [x] Dedicated `infra/terraform/budget.tf` defines `aws_budgets_budget` for account `107207236011`
- [x] Budget creation is optional (`enable_budget_alerts = false` default) with configurable alert email
- [x] Four notification blocks enforce exact dollar thresholds ($5, $8, $10 actual, $10 forecasted) using `ABSOLUTE_VALUE`
- [x] Numeric validation on `monthly_budget_limit_usd` prevents zero or non-positive budget limits
- [x] Root `terraform.tfvars.example` points to `infra/terraform/terraform.tfvars.example`
- [x] Static block assertions in `tests/infra/test_budget.py` validate HCL structure, notifications, and constraints
- [x] `terraform fmt -check` and `make -C infra/terraform validate` pass backendless verification
- [x] No AWS resources are planned or applied (infrastructure remains unapplied/offline)
- [ ] Pull request #20 reviewed and merged

## Decisions

- Budget alerts are implemented as account-level safety nets (Layer 3) without coupling to the application-side $7.50 runtime DynamoDB admission gate (Layer 2).
- Notification thresholds use `ABSOLUTE_VALUE` with `GREATER_THAN` to preserve the explicit $5/$8/$10 requirement contract even if monthly budget limits change.
- Monitored account ID is constrained to `107207236011` with a lifecycle precondition.
- Test assertions in `tests/infra/test_budget.py` execute robust static block assertions against Terraform source files; Terraform backendless `validate` serves as the authoritative semantic gate.

## Known limitations

- Infrastructure configuration is validated offline (`init-backendless` and `validate`); no live AWS resources are planned or applied in this PR.
- Forecasted alerts require historical account telemetry in AWS Cost Management and may not trigger immediately on newly provisioned accounts.

## Next milestone

Next milestone: Milestone 4 — first real Bedrock call owned by the application, with no tools.

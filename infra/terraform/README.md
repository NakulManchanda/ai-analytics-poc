# Terraform foundation

This directory contains the Milestone 13 AWS foundation only. It provisions
networking, image repositories, private storage, durable-state storage, task
roles, an ECS cluster, and log groups. It does not deploy services, images, an
ALB, CloudFront, or application secrets.

## Local AWS authentication

For this single-user POC, the Terraform operator may use an already-authorized
local AWS CLI default profile. Verify the intended account before any
AWS-dependent command:

```bash
aws sts get-caller-identity
```

AWS SSO remains an optional future hardening path. Do not commit AWS
configuration, credentials, session tokens, or an `AWS_PROFILE` setting. Never
put credentials in `terraform.tfvars`, a container environment, GitHub Actions,
or the repository.

Deployed containers do not use the local profile. They use the separate ECS
task roles created here.

## Commands

Copy `terraform.tfvars.example` to the ignored local `terraform.tfvars`, choose
the target Region/AZs, and then run:

```bash
make -C infra/terraform init-backendless
make -C infra/terraform fmt-check
make -C infra/terraform validate
make -C infra/terraform plan
```

`make plan` is read-only. Review its output before any separately approved
apply; this Makefile intentionally has no apply target.

After an approved apply, retrieve exact console links with:

```bash
terraform output -json console_links
```

See [the console-links guide](../../docs/aws-console-links.md) for the
post-apply workflow and clickable Markdown rendering command.

## Optional AWS Budget alerts

To enable the account-level spend alerts (Layer 3 billing safety net):

1. Set `enable_budget_alerts = true` in local `terraform.tfvars` or pass `-var="enable_budget_alerts=true"`.
2. Set `budget_alert_email` to your notification email in local `terraform.tfvars` or via `TF_VAR_budget_alert_email`. Do not commit real email addresses or secrets.
3. The budget enforces account `107207236011` with a monthly cap (`monthly_budget_limit_usd`, default `"10.0"`) and provisions 4 notifications with `threshold_type = "ABSOLUTE_VALUE"`:
   - $5 actual spend (`GREATER_THAN` $5.00 actual)
   - $8 actual spend (`GREATER_THAN` $8.00 actual)
   - $10 actual spend (`GREATER_THAN` $10.00 actual)
   - $10 forecasted spend (`GREATER_THAN` $10.00 forecasted)

> **Note on Forecast Alerts:** AWS Budgets forecasted notifications require historical account usage telemetry. On brand-new AWS accounts without historical usage data, forecasted alerts may not trigger until sufficient billing history has accumulated. Actual spend alerts fire as usage is recorded.

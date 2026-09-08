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

## Remote state and migration

Terraform state moves to a private, versioned, encrypted S3 bucket with Block
Public Access, a TLS-only policy, and Terraform native S3 lockfiles. The bucket
is isolated in `bootstrap/` because Terraform cannot use a backend it creates.

The bootstrap and migration are manual protected operations: public PRs and
ordinary merges never receive AWS credentials or remote-state access.

1. In `infra/terraform/bootstrap`, copy `terraform.tfvars.example` to an
   ignored local file, set a globally unique bucket name, and run a privately
   reviewed apply only after explicit human approval.
2. In `infra/terraform`, copy `backend.hcl.example` to ignored `backend.hcl`.
   Back up authoritative local state in an encrypted operator-controlled
   location. Record only its checksum and S3 object version in a private record.
3. Run `make init-remote`; migrate only after reviewing the source and
   destination. Confirm privately with `terraform state list` and S3 versioning.
4. Retain the local backup until an approved remote plan and drift check agree.
   Roll back through the previous S3 object version, never by casually copying
   a state file. For emergency recovery, inspect an isolated backup with
   `terraform init -backend=false` and restore through the protected procedure.

State, plans, backend files, tfvars, environment values, resource identifiers,
and Terraform output never belong in commits, artifacts, or PR comments.

## Commands

Copy `terraform.tfvars.example` to the ignored local `terraform.tfvars`, choose
the target Region/AZs, and then run:

```bash
make -C infra/terraform init-backendless
make -C infra/terraform fmt-check
make -C infra/terraform validate
make -C infra/terraform plan
```

After migration use `make init-remote` for local operations. `make plan` is
read-only; review it only in a private operator session before a separately
approved apply.

## GitHub release apply

Public validation remains backendless and credential-free. The manual
**Approved Terraform release** workflow runs only in the protected
`terraform-production` environment. Configure protected environment variables
for backend details, Region/network inputs, and the OIDC role ARN. The trust
policy restricts tokens to this repository, protected environment, and the
release workflow on `main`; it uses no static AWS keys.

Dispatch only an approved exact `v*` tag with `demo_enabled=true`. Environment
approval occurs before OIDC credentials. The job saves the plan on the approved
runner, emits only add/change/destroy counts, and applies that same file. It
never uploads a plan artifact. Do not split plan/apply across jobs: an artifact
would be an unnecessary public-repository exposure path.

Run `terraform plan -refresh-only` privately against the remote backend before
a release to check drift; investigate drift before applying.

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
3. The budget enforces account `<aws-account-id>` with a monthly cap (`monthly_budget_limit_usd`, default `"10.0"`) and provisions 4 notifications with `threshold_type = "ABSOLUTE_VALUE"`:
   - $5 actual spend (`GREATER_THAN` $5.00 actual)
   - $8 actual spend (`GREATER_THAN` $8.00 actual)
   - $10 actual spend (`GREATER_THAN` $10.00 actual)
   - $10 forecasted spend (`GREATER_THAN` $10.00 forecasted)

> **Note on Forecast Alerts:** AWS Budgets forecasted notifications require historical account usage telemetry. On brand-new AWS accounts without historical usage data, forecasted alerts may not trigger until sufficient billing history has accumulated. Actual spend alerts fire as usage is recorded.

## Park between demos

See [the park/resume runbook](../../docs/demo-runtime.md). Parking is a
shutdown-only operation; resuming paid infrastructure requires this protected
tag workflow or explicit human approval.

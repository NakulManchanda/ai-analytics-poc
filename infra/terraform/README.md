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

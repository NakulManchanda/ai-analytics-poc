# AWS console links after Terraform apply

The Milestone 13 resources do not exist until an approved `terraform apply`
has completed. This document intentionally does not contain guessed resource
IDs or bucket names.

The Terraform configuration restricts this single-user POC foundation to AWS
account `107207236011` and generates the exact AWS Console URLs using the
configured `aws_region`. Verify the signed-in account in the console before
opening a link.

After apply, run from the repository root:

```bash
terraform -chdir=infra/terraform output -json console_links
```

The resulting object contains fully resolved, URL-encoded links for the VPC,
both public and private subnets, ECS task security group, both ECR repositories,
both S3 buckets, DynamoDB table, ECS cluster, all three IAM roles, and both
CloudWatch log groups.

To render the actual output as clickable Markdown links (requires `jq`):

```bash
terraform -chdir=infra/terraform output -json console_links \
  | jq -r 'to_entries[] | "- [\(.key)](\(.value))"'
```

Every generated URL has the exact resource identifier produced by apply and
routes to the configured Region; CloudWatch log-group names and other path
components are URL-encoded by Terraform before output.

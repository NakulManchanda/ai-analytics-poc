# Demo Runtime: Park and Resume

The demo is **live** today. This runbook describes the pending park/resume
change; no Terraform apply has been run yet. Issue #86 has an in-progress
draft PR.

## State and safety

`infra/terraform/terraform.tfstate` in the main checkout is authoritative.
The state in this worktree is a planning snapshot only: never apply it and
never copy it back. Integrate the implementation with the authoritative state
before creating a fresh plan for an apply.

Set the persisted local `infra/terraform/terraform.tfvars` value to control
the runtime:

```hcl
demo_enabled = false # park
# demo_enabled = true # resume
```

The variable defaults to `true` for compatibility. `ai_app_image_tag` and
`analytics_mcp_image_tag` accept either an ECR tag or a `sha256:` digest. For
recovery, pin the observed running images in the local `terraform.tfvars`:

```hcl
ai_app_image_tag        = "sha256:9f9de9e0b36134ec9f86ced47846b8f8480f4e85fc99453c0b7e990b0ce4fa08"
analytics_mcp_image_tag = "sha256:234bcabadcdb29175c10a0c87c08225f529362951253e05c3cf40e1ebf8cc40d"
```

Do not substitute mutable `latest`. A future deployment is responsible for
updating image variables.

## Park

1. Drain or cancel active work before proceeding. Redis is transient, so its
   in-flight events and cancellation flags are lost; DynamoDB conversations,
   messages, runs, and steps remain.
2. Set `demo_enabled = false` in the persisted local `terraform.tfvars`.
3. From `infra/terraform`, run `make runtime-plan`. It writes the ignored
   `runtime.tfplan`; `make plan` remains available for a normal plan.
4. Review the saved plan. Parking conditionally removes `aws_lb.main`,
   `aws_lb_listener.http`, `aws_elasticache_cluster.redis`,
   `aws_ecs_task_definition.ai_app`, and both ECS services. It retains data,
   ECR, CloudFront, VPC, IAM, and the target group.
5. With the user's explicit authorization, apply exactly that saved plan with
   `make runtime-apply`.

While parked, static UI remains available. The CloudFront function returns a
JSON `503` “demo offline” response for `/api/*`.

## Resume

After the PR merges, work from the main checkout and its authoritative local
state. Preserve unrelated local changes; do not use or copy this worktree's
planning snapshot.

```sh
git pull --ff-only
cd infra/terraform
# Edit terraform.tfvars: demo_enabled = true, retaining the pinned image digests.
make runtime-plan
make runtime-apply
aws ecs wait services-stable --cluster ai-analytics-poc-demo-cluster \\
  --services ai-analytics-poc-demo-ai-app ai-analytics-poc-demo-analytics-mcp
curl -i https://ai.sibkaro.com/
curl -i https://ai.sibkaro.com/api/
```

Review the saved plan before `runtime-apply`. The authorization to apply has
been granted for this change. Wait a few minutes for ECS, ALB, and CloudFront
routing. Confirm `/` serves the static UI and `/api/` no longer returns the
parked JSON `503` “demo offline” response; no application health route is
assumed by this check.

Resume restores the removed runtime resources and `/api/*` routing. The rough
runtime estimate is about $3 pre-tax for 20 hours; the $5 budget is not a
guarantee, and this month's spend is already about $20.

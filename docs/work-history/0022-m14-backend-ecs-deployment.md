# Work 0022 — Milestone 14 backend deploy to ECS/Fargate + ALB

## Goal

Provision and configure the AWS Application Load Balancer (ALB), ECS/Fargate services (`ai-app` and `analytics-mcp`), and private AWS Service Connect namespace for zero-NAT, cost-efficient cloud execution.

## Starting state

`origin/main` at `f21061e` has Milestones 0–12 merged (including local integration hardening and multi-service smoke).

## Decisions

- **Application Load Balancer (`infra/terraform/alb.tf`)**:
  - Provisioned internet-facing ALB in public subnets with HTTP listener forwarding to `ai-app` target group (port 8080, `/health` check).
  - Configured ALB security group with ingress port 80 from `0.0.0.0/0` and egress to VPC CIDR on port 8080.
- **ECS Task Definitions & Services (`infra/terraform/ecs.tf`)**:
  - Configured `ai-app` (0.25 vCPU, 512MB RAM) with IAM task role allowing Bedrock invocation, DynamoDB state table, and S3 artifact bucket.
  - Configured `analytics-mcp` (0.25 vCPU, 512MB RAM) with IAM task role having no AWS data permissions.
  - Established private Service Connect HTTP namespace (`ai-analytics-poc.local`) allowing `ai-app` to reach `analytics-mcp` on `http://analytics-mcp:8001/mcp`.
  - Configured Fargate tasks with `assign_public_ip = true` in public subnets to enable outbound ECR pulls, AWS Bedrock calls, and public NYC TLC parquet downloads with $0 NAT Gateway overhead.
- **Security Groups (`infra/terraform/network.tf`)**:
  - Restructured ECS tasks security group with ingress on port 8080 from ALB and port 8001 from self for Service Connect.
- **Outputs (`infra/terraform/outputs.tf`)**:
  - Added `alb_dns_name`, `alb_arn`, `ai_app_service_name`, `analytics_mcp_service_name`, `service_connect_namespace`, and console links.
- **Smoke & Test Suite**:
  - Created `scripts/smoke/14_ecs_backend_smoke.sh` and added `make ecs-smoke`.
  - Created `tests/infra/test_ecs_deployment.py` asserting Terraform HCL resource definitions.

## Verification

- `terraform fmt -check` and `terraform validate` passed cleanly.
- `make test` passed 106 backend tests and 10 React vitest tests.

## Pull request and merge state

Branch `feat/m14-ecs-backend` tracks [issue #29](https://github.com/NakulManchanda/ai-analytics-poc/issues/29).

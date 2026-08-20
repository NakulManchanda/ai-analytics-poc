# Current milestone

Milestone 14 — backend deploy to ECS/Fargate + ALB

## Status

IN PROGRESS — [issue #29](https://github.com/NakulManchanda/ai-analytics-poc/issues/29)

## Merged milestone baseline

- **Milestones 0–12**: Merged (`f21061e`), including local multi-service integration hardening, async job worker, Redis Streams SSE, and bounded-context visualization.
- **Milestone 13 Foundation**: Terraform infrastructure foundation and budget alerts merged.

## Acceptance criteria

- [x] Application Load Balancer (`aws_lb.main`) and target group provisioned across public subnets.
- [x] ECS task definitions for `ai-app` and `analytics-mcp` defined with least-privilege IAM roles and Fargate compute.
- [x] Private Service Connect namespace (`ai-analytics-poc.local`) configured for app→MCP communication.
- [x] Zero-NAT task networking configured with public IPs enabled for cost efficiency ($0 NAT overhead).
- [x] Security group ingress/egress boundaries enforced (ALB→app on 8080, app→MCP on 8001).
- [x] `scripts/smoke/14_ecs_backend_smoke.sh` and `make ecs-smoke` added.
- [x] Full test suite passing across all services (`make test`).

## Next milestone

Milestone 15 (S3 + CloudFront frontend, #30) gates on Milestone 14 merging.

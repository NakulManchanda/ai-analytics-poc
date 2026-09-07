# 0045 — Terraform demo runtime parking

## Goal and starting point

Issue #86 authorizes parking the paid backend between tests and a next-session
restart note. The existing architecture was live continuously at about $78/month
before tax. Main baseline: `0d02a73`. Owner: NakulManchanda (Codex);
branch `codex/demo-parking`, worktree `.worktrees/demo-parking`.

## Decisions

- One persisted `demo_enabled` input; default true preserves existing deployments.
- False removes both ECS services, the app task definition, ALB/listener, and Redis
  node. Retain DynamoDB, S3, ECR, CloudFront, networking, IAM and target group.
- Moved blocks preserve existing Terraform resource identity on enabling count.
- Parked API requests receive JSON 503 from a CloudFront viewer-request function;
  resume restores the uncached ALB origin and separate app/MCP services.
- Image inputs accept immutable digests; local operator pins the observed running
  images for restart. No credentials or local state are committed.
- Fix pre-existing mixed inline/standalone ECS egress ownership: inline TCP 6379
  to private cache subnets avoids the dependency cycle; Redis inbound remains
  restricted to the ECS security group. Otherwise apply would revoke Redis access.
- Use authoritative main-checkout local state only after PR integration. Worktree
  snapshots and saved plans are for inspection, never copied back or applied.

## Verification and status

- `make -C infra/terraform fmt-check validate`: passed.
- Local Node assertion against the actual function code: GET/POST/OPTIONS/DELETE
  all return 503, JSON content type and no-store cache control.
- Refreshed Terraform plans for `demo_enabled=false` and `true` inspected; data
  stores retained, enabled mode keeps existing runtime identities.
- `git diff --check` and changed-file secret/path scan completed before commit.
- Draft PR, exact-head GitHub Actions and independent Claude review pending.
- User explicitly authorized local apply and live shutdown verification; apply
  will follow PR integration. No apply performed at this entry's initial commit.

## Lessons and restart

Scaling Fargate alone leaves ALB and Redis billing. Park the paid runtime, not the
whole stack. Retained DynamoDB state survives; Redis in-flight work does not.
Read [the runbook](../demo-runtime.md) and [handoff](../next-session-handoff.md)
for exact start/stop commands and recorded deployment status.

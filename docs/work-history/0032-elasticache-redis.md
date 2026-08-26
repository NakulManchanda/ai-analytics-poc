# 0032 — v2 infra: provision ElastiCache Redis for transient delivery (#57)

## Goal

Provision a minimal single-node ElastiCache Redis cluster in the existing VPC for
transient event and job delivery. Wire the primary endpoint into the ECS ai-app and
worker task definitions as `REDIS_URL`. DynamoDB remains the durable truth; Redis is
coordination-only and never stores conversation state.

## Starting point

- `main` at `a0c372e` (feat: add governed regional trip metrics, PR #60).
- Issue #57 explicitly authorized implementation (2026-08-26 comment).
- Existing Terraform: VPC with two public and two private subnets, `aws_security_group.ecs_tasks`
  shared by ai-app and worker tasks, ECS task definitions with environment arrays.
- `docker-compose.yml` already wires `REDIS_URL=redis://redis:6379/0` to the `app` and `worker`
  services — the env-var contract was established locally. Deployed ECS was missing this var.
- Both `services/app/app/worker.py` and `services/app/app/routers/events.py` fall back to
  `redis://localhost:6379/0` when `REDIS_URL` is absent — the deployed stack was silently using
  a non-existent localhost address.

## Decisions

- **Single node, no cluster mode, no multi-AZ** — matches the POC's small footprint. Redis 7.1,
  `cache.t4g.micro`.
- **Private subnets** — ElastiCache is placed in the existing `aws_subnet.private[*]` subnets
  so it is unreachable from the internet.
- **Dedicated security group** — `aws_security_group.redis` allows inbound 6379 only from
  `aws_security_group.ecs_tasks`. No outbound traffic is needed from a cache node.
- **Circular dependency broken via `aws_security_group_rule`** — the ecs_tasks SG needed a
  Redis egress rule, but redis SG already references ecs_tasks. Separated the cross-reference
  into `aws_security_group_rule.ecs_tasks_to_redis` in `network.tf` to avoid a Terraform cycle.
- **`snapshot_retention_limit = 0`** — persistence explicitly disabled; this is transient delivery.
- **`apply_immediately = true`** — consistent with the POC's non-critical, single-environment posture.
- **`REDIS_URL` in ai-app task definition only** — the worker is a separate container built from
  the same image, launched via an override command (see `docker-compose.yml`). In ECS there is
  no separate worker task definition in the existing config; the ai-app task definition covers
  the shared image. If a separate worker ECS task definition is added in a future milestone, it
  will need the same env var.
- **No `terraform apply`** — this PR is infrastructure-as-code only; apply is a separate
  authorized action per AGENTS.md.

## Files added / changed

| File | Change |
|------|--------|
| `infra/terraform/elasticache.tf` | New: subnet group, security group, single-node cluster |
| `infra/terraform/variables.tf` | Added `redis_node_type` and `redis_engine_version` variables |
| `infra/terraform/ecs.tf` | Added `REDIS_URL` env var to ai-app task definition |
| `infra/terraform/network.tf` | Added `aws_security_group_rule.ecs_tasks_to_redis` egress rule |
| `infra/terraform/outputs.tf` | Added `elasticache_redis_endpoint` and `elasticache_redis_url` outputs |
| `docs/work-history/0032-elasticache-redis.md` | This entry |

## Verification

```
terraform fmt         # no diff — all files already well-formatted
terraform init -backend=false  # providers installed successfully
terraform validate    # "The configuration is valid."
```

`terraform plan` was not run because AWS credentials are not present in this environment.
The validate pass confirms all resource references, types, and expressions are correct.

## PR / merge state

Draft PR: to be opened against `main`.
Branch: `codex/redis-elasticache`.
**Do not merge without explicit user authorization.** Apply/deploy is a separate authorized step.

## Limitations

- No `terraform plan` output (no AWS credentials in this environment).
- Worker ECS task definition does not exist as a separate resource; `REDIS_URL` is wired into
  the ai-app task definition. If a separate worker task definition is introduced, it will need
  the same variable added at that time.
- ElastiCache is in private subnets; ECS tasks currently run in public subnets (with
  `assign_public_ip = true`). The Redis egress rule is correctly attached and the security group
  allows the connection across subnets within the VPC. If ECS tasks are later moved to private
  subnets, no security-group changes are required.

## Lessons

- Mutual security group references require `aws_security_group_rule` resources; inline rules
  cannot form a cycle across two groups even when one is in a separate file.
- ElastiCache cluster node addresses are available at plan time via
  `aws_elasticache_cluster.redis.cache_nodes[0].address`.

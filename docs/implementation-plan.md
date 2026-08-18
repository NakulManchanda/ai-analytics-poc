# Implementation plan

The project advances through one reviewable vertical slice at a time. A milestone begins only
when explicitly requested, and ends after its acceptance checks, documentation update, and
manual verification instructions are complete. Do not start the next milestone automatically.

## Milestones

| Milestone | Deliverable | Explicit boundary |
|---|---|---|
| 0 | Minimal FastAPI `GET /health` service | No MCP, React, Redis, AWS, or LLM |
| 1 | Independently runnable empty FastMCP service | No dataset or LLM |
| 2 | Reproducible NYC TLC Parquet dataset spike with DuckDB | Isolated from AI |
| 3 | Minimal React shell showing the application workflow | No real LLM |
| 4 | First Bedrock call owned by the application | No tools |
| 5 | One-turn LLM-to-MCP tool execution | No general loop |
| 6 | Governed, read-only analytical query tool | No arbitrary user SQL |
| 7 | Durable conversation, message, and run persistence | Redis is not durable state |
| 8 | Bounded multi-step orchestration loop | Enforce iteration, tool, time, token, and cost limits |
| 9 | Redis Streams coordination and browser SSE | Durable state remains authoritative |
| 10 | Bounded working-context visualization | Do not expose hidden reasoning |
| 11 | One async job example | Keep worker scope narrow |
| 12 | Local integration hardening and cumulative smoke checks | No AWS deployment yet |
| 13 | Terraform foundation | Infrastructure only |
| 14 | Backend deployment to ECS/Fargate | App and MCP remain separate services |
| 15 | Private S3 frontend plus CloudFront | `/api/*` routes to ALB without caching |
| 16 | Final demo, security, cost, and documentation review | Fix only demo-breaking gaps |

## Milestone workflow

1. Read the milestone in `ai_analytics_poc_requirements_aws_v5.md` and inspect repository state.
2. State the smallest implementation needed and avoid future milestone scaffolding.
3. Create a dedicated branch and worktree after the bootstrap commit.
4. Implement the slice with externally meaningful tests.
5. Run the milestone acceptance checks and provide an exact manual demo command.
6. Update `README.md`, `docs/progress.md`, and the matching work-history entry.
7. Open and review a pull request; after merge, run the cumulative smoke suite.
8. Stop and wait for the next milestone request.

## Cross-cutting invariants

- CloudFront is the eventual public entry point: `/*` serves private S3 assets and `/api/*`
  reaches the app service through an ALB.
- The app owns Bedrock calls, loop budgets, and durable DynamoDB state.
- The private MCP service owns bounded, read-only DuckDB access to local Parquet data.
- Redis carries transient coordination events and can be rebuilt from durable state.
- ECS execution roles pull images and emit logs; task roles grant only application permissions.
- No static AWS credentials, committed secrets, unnecessary NAT, or production-scale platform.

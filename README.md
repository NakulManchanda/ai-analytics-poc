# AI Analytics POC

A small production-shaped AI analytics application exploring durable agent orchestration,
MCP tool boundaries, streaming UX, bounded context, and cost-aware execution.

This repository is being built incrementally. Milestone 3 adds a small React shell that visibly
checks the FastAPI and FastMCP path without adding prompt execution, an LLM, persistence, or SSE.

## Intended architecture

The completed POC will use one CloudFront hostname. Static React assets will be served from a
private S3 bucket, while `/api/*` requests will pass through an ALB to an `ai-app` ECS/Fargate
service. A separate, private `analytics-mcp` ECS service will expose bounded read-only tools over
MCP. The application service will own the Bedrock orchestration loop and durable state; Redis
will coordinate transient run events only.

## Delivery approach

Work follows the milestones in [the implementation plan](docs/implementation-plan.md). Each
functional milestone gets a branch, pull request, verification evidence, and a work-history
entry. The active handoff is maintained in [the progress file](docs/progress.md), and durable
architecture choices belong in [the decision log](docs/decisions/README.md).

## Security and scope

- Authentication is intentionally out of scope. The public deployment will contain only a
  public dataset and read-only analytical tools.
- AWS workloads will use short-lived role credentials. Long-lived AWS access keys and secret
  values must never be committed.
- The POC deliberately excludes Kubernetes, Kafka, RDS, OpenSearch, EFS, and vector databases.
- The deployment will use strict run budgets and a global Bedrock allowance to contain cost.

## Local development

Install dependencies and run the FastAPI service on port 8080:

```bash
uv sync --project services/app --dev
make dev
```

Run the MCP service separately on port 8001 with `make mcp-dev`. Its focused contract check is
`make mcp-smoke`; `make smoke` runs both the M0 health and M1 MCP checks.

In another terminal, verify the exact health contract:

```bash
make smoke
```

Run the automated test suite with `make test`. Useful repository commands are listed by:

```bash
make help
```

### React shell (Milestone 3)

Build and run the local browser path with Docker Compose:

```bash
docker compose up --build
```

Open http://localhost:3000. The page uses a same-origin `/api/status` request; Nginx proxies that
request to FastAPI, and FastAPI uses the MCP protocol to discover the separate FastMCP service.
It should display **Backend ready** and **MCP discovered · 1 tools · 1 resources**. The disabled
prompt and timeline are visual placeholders only; they send no data and invoke no LLM.

Run the focused browser package checks with:

```bash
cd web
npm test
npm run build
```

### Dataset spike (Milestone 2)

The pinned official artifact metadata lives in
[`config/datasets/nyc_yellow_taxi_2024_01.toml`](config/datasets/nyc_yellow_taxi_2024_01.toml).
The Parquet and lookup CSV are cached only under ignored `data/`; each file is verified against its
recorded SHA-256 before reuse. Run the fixture-backed tests with `make dataset-test`.

To download (or reuse) the local cache and run the deterministic profile, including the schema,
exact row counts, bounded day/zone aggregation, zone join, timing, and process high-water RSS:

```bash
make dataset-smoke
```

The FastMCP service initializes that same pinned profile at startup and exposes
`dataset://nyc-taxi/schema` plus `get_dataset_profile()`. Run `make mcp-smoke` to verify capability
discovery without downloading data; the fixture-backed protocol test covers the schema and profile
payload. `make smoke` cumulatively runs M0, M1, and the M2 dataset check. Ordinary CI runs only
fixture tests and never downloads the 50 MB public Parquet artifact. This milestone accepts no
user SQL.

## AWS lifecycle warning

**Destroy the AWS environment when the demo week is over to prevent ongoing charges.**
Milestone 13 will introduce a `make destroy` target with the Terraform implementation. After
that milestone, review the plan and run:

```bash
make destroy
```

## How this project evolved

- [Implementation plan](docs/implementation-plan.md)
- [Work-history ledger](docs/work-history/README.md)
- [Architectural decisions](docs/decisions/README.md)

The canonical source requirements are tracked in
[`ai_analytics_poc_requirements_aws_v5.md`](ai_analytics_poc_requirements_aws_v5.md). The
historical bootstrap phase is complete; subsequent requirement changes follow the normal branch
and pull-request workflow.

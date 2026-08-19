# AI Analytics POC

A small production-shaped AI analytics application exploring durable agent orchestration,
MCP tool boundaries, streaming UX, bounded context, and cost-aware execution.

This repository is being built incrementally. The current merged baseline includes Milestone 0 (FastAPI health), Milestone 1 (empty FastMCP service), Milestone 2 (DuckDB dataset profile and FastMCP tools/resources), Milestone 3 (React UI status shell), and Milestone 13 Terraform foundation. This work adds optional AWS Budgets alerting configuration under Milestone 13 without mutating or applying live AWS infrastructure.

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

### Infrastructure and Budget Alerts (Milestone 13)

The Terraform configuration under [`infra/terraform/`](infra/terraform/) defines the VPC, subnets,
ECR, S3, DynamoDB, ECS cluster, IAM roles, CloudWatch log groups, and optional AWS budget alerts
(`infra/terraform/budget.tf`).

Verify Terraform syntax, formatting, and validation offline without configuring remote state:

```bash
make -C infra/terraform init-backendless
make -C infra/terraform fmt-check
make -C infra/terraform validate
```

To configure optional budget alerts locally in an ignored `terraform.tfvars`:

```hcl
enable_budget_alerts = true
budget_alert_email   = "alerts@example.com"
```

All infrastructure remains unapplied offline configuration until explicitly authorized in deployment milestones.

## AWS lifecycle warning

**Destroy the AWS environment when the demo week is over to prevent ongoing charges.**
Milestone 13 defines the Terraform foundation under `infra/terraform/`. When infrastructure is deployed
to AWS in future deployment milestones, review the plan and destroy resources using:

```bash
terraform -chdir=infra/terraform destroy -var-file=terraform.tfvars
```

## How this project evolved

- [Implementation plan](docs/implementation-plan.md)
- [Work-history ledger](docs/work-history/README.md)
- [Architectural decisions](docs/decisions/README.md)

The canonical source requirements are tracked in
[`ai_analytics_poc_requirements_aws_v5.md`](ai_analytics_poc_requirements_aws_v5.md). The
historical bootstrap phase is complete; subsequent requirement changes follow the normal branch
and pull-request workflow.

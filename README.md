# AI Analytics POC

A small production-shaped AI analytics application exploring durable agent orchestration,
MCP tool boundaries, streaming UX, bounded context, and cost-aware execution.

This repository is being built incrementally. The merged baseline includes Milestone 0 (FastAPI
health), Milestone 1 (empty FastMCP service), Milestone 2 (DuckDB dataset profile and FastMCP
tools/resources), Milestone 3 (React UI status shell), Milestone 4 (bounded Bedrock call),
Milestone 5 (one-turn profile sequence), and Milestone 13 Terraform foundation with optional AWS
Budgets alerting configuration. Milestone 6 adds a governed read-only `query_taxi_data` tool
and extends the application flow with dataset schema context and bounded DuckDB analytical queries.

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

Host-facing ports are overrideable without changing internal container ports: service-specific
variables win over the generic fallback, so `APP_PORT=8082 PORT=9000 make dev` uses 8082,
`PORT=8002 make mcp-dev` uses 8002, and Docker Compose uses `WEB_PORT`, then `PORT`, then 3000.

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

### Governed query Bedrock-to-MCP path (Milestone 6)

`POST /api/ask` accepts a non-empty `prompt` (up to 4,000 characters) and runs exactly one fixed
governed query sequence: FastAPI supplies the MCP dataset schema resource (`dataset://nyc-taxi/schema`)
to Bedrock call #1, which proposes `query_taxi_data` with validated `{analysis, limit}` arguments
(no caller-supplied SQL or paths). FastAPI validates that proposal, invokes `query_taxi_data` on
FastMCP, and receives a bounded result envelope (columns, rows, duration, `query_id`, and `truncated`).
Bedrock call #2 synthesizes the final answer using only that bounded envelope.

The app requires `LLM_PROVIDER=bedrock`, `AWS_REGION=us-east-1`, and
`LLM_MODEL_ID=amazon.nova-micro-v1:0`; deployed containers use the `ai-app` ECS task role and the
AWS SDK default credential chain, never static credentials. The local Docker Compose file sets a
deterministic `LLM_PROVIDER=fake` only for its no-cost browser/API/MCP smoke path.

Run the focused unit tests without making an AWS call:

```bash
uv run --project services/app pytest services/app/tests
```

The following command makes exactly two paid, bounded (128 output-token maximum per call)
Bedrock calls plus one local FastMCP query call after verifying the active AWS STS account is
`107207236011`. It is intentionally excluded from `make smoke`, requires an explicit environment
opt-in, and fails unless the exact two-call sequence, tool ID, query ID, aggregate usage, and
aggregate latency contract are satisfied:

```bash
RUN_BEDROCK_SMOKE=1 make m6-bedrock-smoke
```

Terraform validates this exact region/model foundation-model ARN for the narrow `ai-app` allowlist.
The `analytics-mcp` task role has no Bedrock invocation policy. Expected Bedrock, credentials, or
configuration failures return a controlled response with `retryable` and `llm_call_id` metadata;
provider details are not returned to callers.

### React workflow (Milestone 6)

Build and run the local browser path with Docker Compose:

```bash
docker compose up --build
```

Open http://localhost:3000. The page uses a same-origin `/api/status` request; Nginx proxies that
request to FastAPI, and FastAPI uses the MCP protocol to discover the separate FastMCP service.
It displays **Backend ready** and **MCP discovered · 2 tools · 1 resources**. Enter an analytical
question (e.g. "Which pickup zones have the most trips?") and select **Run analysis** to execute
the governed query flow with token/latency metadata.

The automated Compose smoke defaults to a Docker-assigned ephemeral host port and an isolated
`COMPOSE_PROJECT_NAME`, so parallel runs do not contend for port 3000 or clean up another local
stack. Pin an explicit host port only when needed:

```bash
WEB_PORT=3001 make compose-smoke
```

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

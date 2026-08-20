# AI Analytics POC

A production-grade, cost-governed AI Analytics platform featuring durable agent orchestration, strict Model Context Protocol (MCP) service boundaries, real-time Server-Sent Events (SSE) streaming UX, bounded working-context inspection, and zero-NAT AWS cloud infrastructure.

```
                                      +----------------------------------------------------+
                                      |                   Amazon CloudFront                |
                                      |            https://<distribution>.cloudfront.net   |
                                      +-------------------------+--------------------------+
                                                                |
                                       +------------------------+------------------------+
                        /* (Static SPA Assets)                                 /api/* (Dynamic API & SSE)
                                       |                                                 |
                       +---------------+---------------+                 +---------------+---------------+
                       |        Private S3 Bucket      |                 |   Application Load Balancer   |
                       |    (Origin Access Control)    |                 |             (ALB)             |
                       +-------------------------------+                 +---------------+---------------+
                                                                                         |
                                                                         +---------------+---------------+
                                                                         |   ECS Fargate: ai-app (8080)  |
                                                                         |   - Bedrock Nova Micro Loop   |
                                                                         |   - Execution Budgets & State |
                                                                         +-------+---------------+-------+
                                                                                 |               |
                                      +------------------------------------------+               +-----------------------+
                                      | (AWS Service Connect: port 8001)                         | (Transient Events)    | (Durable State)
                                      v                                                          v                       v
                      +---------------+---------------+                          +---------------+---+   +---------------+---+
                      | ECS Fargate: analytics-mcp    |                          |       Redis       |   | Amazon DynamoDB   |
                      | - FastMCP Server              |                          |   Streams & Queue |   | Application State |
                      | - Read-only DuckDB Engine     |                          +-------------------+   +-------------------+
                      | - Local NYC TLC Parquet Data  |                                  ^
                      +-------------------------------+                                  | (Job Dequeue)
                                                                                 +-------+-------+
                                                                                 |  ECS Worker   |
                                                                                 +---------------+
```

---

## Key Architectural Principles

1. **Strict FastMCP Service Boundary**:
   - The FastAPI `ai-app` service owns all LLM calls, orchestration loops, durable state transitions, and execution budgets.
   - The `analytics-mcp` service exposes bounded, read-only analytics capabilities over the Model Context Protocol using DuckDB on local Parquet files.
   - The MCP server **never** makes LLM calls, connects to Redis, or receives caller-supplied SQL.
2. **Authoritative Durable State vs. Transient Coordination**:
   - **Amazon DynamoDB** is the single source of truth for persistent conversation threads, messages, execution runs, and asynchronous jobs.
   - **Redis** is used strictly for transient pub/sub streaming (`ai-analytics:runs:<id>:events`) and background job queueing. Redis state can be discarded without losing durable conversation history.
3. **Bounded Agent Loop with Strict Budgets**:
   - Multi-step orchestration loop (up to 5 iterations) governed by hard limits on wall-clock execution time (30s), total tool invocations (3), token consumption, and tool output byte size.
   - Context reducer compresses intermediate tool observations into a bounded working context, preventing prompt bloat.
4. **Real-Time Streaming UX & Working-Context Inspector**:
   - Live Server-Sent Events (SSE) feed step-by-step progress, tool proposals, and execution logs to the React UI in real time.
   - Built-in UI Inspector tabs display dataset schema, tool parameters, token usage, latency breakdowns, and raw event replays.
5. **Zero-NAT Cost-Efficient AWS Infrastructure**:
   - ECS Fargate tasks run in public subnets with `assign_public_ip = true` to communicate with AWS Bedrock, ECR, and S3 directly over the Internet Gateway, completely eliminating costly AWS NAT Gateway overhead ($0/mo NAT cost).

---

## Repository Structure

```text
ai_analytics_poc/
├── services/
│   ├── app/                 # FastAPI orchestration server & async worker
│   │   ├── app/
│   │   │   ├── orchestration/ # Agent loop, execution budgets, Bedrock client
│   │   │   ├── storage/       # DynamoDB state repository & Redis Streams
│   │   │   └── routers/       # /api/ask, /api/jobs, /api/runs, /api/health
│   │   └── tests/
│   ├── mcp/                 # FastMCP analytical server (DuckDB + Parquet)
│   │   ├── mcp_server/      # Tools (query_taxi_data, get_dataset_profile)
│   │   └── tests/
│   └── dataset_spike/       # NYC TLC Yellow Taxi dataset profiling & spike
├── web/                     # React 18 + TypeScript + Vite UI
│   ├── src/                 # QueryForm, RunTimeline, ContextInspector, App
│   └── dist/                # Production build artifacts for S3 deployment
├── infra/
│   └── terraform/           # Complete AWS infrastructure (ALB, ECS, S3, CloudFront)
├── scripts/
│   ├── smoke/               # Milestone verification & integration smoke scripts
│   └── deploy_frontend.sh   # S3 sync + CloudFront cache invalidation
└── docs/
    ├── work-history/        # Post-bootstrap monotonic ledger (0001–0024)
    ├── decisions/           # Architectural Decision Records (ADRs)
    └── progress.md          # Active milestone tracking
```

---

## Local Development & Testing

### Prerequisites
- Python 3.12+ and `uv`
- Node.js 24+ and `npm`
- Docker and Docker Compose

### 1. Run Complete 5-Service Stack Locally

Launch the full local system with Docker Compose (Web UI, FastAPI app, FastMCP server, Redis, and Worker):

```bash
docker compose up --build
```

- **React Web UI**: [http://localhost:3000](http://localhost:3000)
- **FastAPI Backend**: [http://localhost:8080/health](http://localhost:8080/health)
- **FastMCP Server**: [http://localhost:8001/mcp](http://localhost:8001/mcp)

### 2. Run All Automated Test Suites

Execute all Python pytest suites (app, MCP, dataset spike, infrastructure) and React vitest tests:

```bash
make test
```

### 3. Run End-to-End Integration Smoke

Run the multi-service integration smoke test verifying health, synchronous `/api/ask`, asynchronous `/api/jobs` worker execution, and historical SSE event replays across isolated containers:

```bash
make integration-smoke
```

---

## AWS Deployment

### 1. Terraform Infrastructure

Initialize and apply the Terraform configuration:

```bash
cd infra/terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 2. Deploy Frontend to S3 & Invalidate CloudFront

Compile the React frontend and upload the build to the private S3 bucket with automatic CloudFront cache invalidation:

```bash
make deploy-frontend
```

### 3. Verify Live Cloud Deployment

Run end-to-end cloud smoke tests against the Application Load Balancer and CloudFront CDN:

```bash
# Verify backend ECS tasks through ALB
make ecs-smoke

# Verify full SPA + SSE streaming through CloudFront HTTPS
make cloudfront-smoke
```

---

## Security & IAM Model

- **No Static Credentials**: All services authenticate via AWS IAM Task Roles using the default AWS SDK credential chain.
- **Least-Privilege Task Roles**:
  - `ai-app` Task Role: Permissions restricted to `bedrock:InvokeModel` on `amazon.nova-micro-v1:0`, `dynamodb:*` on the state table, and `s3:GetObject` on the artifacts bucket.
  - `analytics-mcp` Task Role: Zero AWS data permissions (operates strictly on local container storage).
- **Private S3 Bucket**: Public access blocked completely; accessible strictly by Amazon CloudFront via SigV4 Origin Access Control (OAC).

---

## Teardown & Resource Destruction

To destroy all provisioned AWS cloud resources and prevent ongoing charges:

```bash
terraform -chdir=infra/terraform destroy
```

---

## Work History & Decisions

- [Implementation Plan](docs/implementation-plan.md)
- [Monotonic Work History Ledger](docs/work-history/README.md)
- [Architectural Decisions](docs/decisions/README.md)
- [Progress Log](docs/progress.md)

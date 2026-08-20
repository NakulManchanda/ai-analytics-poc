# AI Analytics POC

A production-grade, cost-governed AI Analytics platform featuring durable agent orchestration, strict Model Context Protocol (MCP) service boundaries, real-time Server-Sent Events (SSE) streaming UX, bounded working-context inspection, and zero-NAT AWS cloud infrastructure.

---

## 🌐 Live Production Endpoints & Verified Architecture

### Production URL Access

- **Primary Custom Subdomain**: [https://ai.sibkaro.com](https://ai.sibkaro.com)
- **Apex Domain**: [https://sibkaro.com](https://sibkaro.com)
- **CloudFront Direct**: `https://d71q2u5j5gxbq.cloudfront.net` *(or `https://db5j03ttoao1a.cloudfront.net`)*

> 📖 **Evaluator Runbook**: See the **[Public Cloud UAT Guide](docs/public-uat-guide.md)** for step-by-step acceptance test scenarios, capability discovery verification, and sample queries. For local development testing, see the **[Local Docker UAT Guide](docs/local-uat-guide.md)**.

### 📊 Verified Live System Status

The deployed platform operates in AWS `us-east-1` as a coordinated 5-component distributed system:

| Layer | Service / Target | Live Health / Status | Verified Capability |
| :--- | :--- | :--- | :--- |
| **Edge CDN** | AWS CloudFront + ACM | `HTTP/2 200 OK` | Custom SSL (`*.sibkaro.com`), SPA routing, `/api/*` cache bypass |
| **Frontend UI** | Amazon S3 + OAC | `HTTP 200 OK` | React 18 SPA, real-time SSE Timeline, Working Context Inspector |
| **Ingress Proxy** | Application Load Balancer | `healthy` | Public path-based routing to internal ECS Fargate tasks |
| **Application Layer** | FastAPI `ai-app` (Fargate) | `status: ok` | Bedrock Claude 3.5 Haiku orchestration, execution budgets, DynamoDB state |
| **Analytical Gateway** | FastMCP `analytics-mcp` | `status: ok (2 tools, 1 resource)` | Read-only DuckDB zero-copy views over 2.96M NYC taxi records via Service Connect |

```
                                      +----------------------------------------------------+
                                      |                 AWS Certificate Manager            |
                                      |              SSL: *.sibkaro.com, sibkaro.com       |
                                      +-------------------------+--------------------------+
                                                                |
                                      +-------------------------v--------------------------+
                                      |                  Amazon CloudFront                 |
                                      |      https://ai.sibkaro.com / https://sibkaro.com  |
                                      +-------------------------+--------------------------+
                                                                |
                                       +------------------------+------------------------+
                        /* (Static React 18 Assets)                            /api/* (Dynamic API & SSE)
                                       |                                                 |
                       +---------------+---------------+                 +---------------+---------------+
                       |        Private S3 Bucket      |                 |   Application Load Balancer   |
                       |    (Origin Access Control)    |                 |             (ALB)             |
                       +-------------------------------+                 +---------------+---------------+
                                                                                         |
                                                                         +---------------+---------------+
                                                                         |   ECS Fargate: ai-app (8080)  |
                                                                         |   - Bedrock Claude 3.5 Haiku  |
                                                                         |   - Execution Budgets & State |
                                                                         +-------+---------------+-------+
                                                                                 |               |
                                      +------------------------------------------+               +-----------------------+
                                      | (AWS Service Connect: port 8001)                         | (Transient Events)    | (Durable State)
                                      v                                                          v                       v
                      +---------------+---------------+                          +---------------+---+   +---------------+---+
                      | ECS Fargate: analytics-mcp    |                          |       Redis       |   | Amazon DynamoDB   |
                      | - FastMCP Server (8001)       |                          |   Streams & Queue |   | Application State |
                      | - Zero-Copy DuckDB Views      |                          +-------------------+   +-------------------+
                      | - 2.96M NYC Taxi Parquet Data |                                  ^
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
   - Live Server-Sent Events (SSE) feed step-by-step progress (`run.received`, `step.tool_proposal`, `step.tool_execution`, `step.final_answer`, `run.completed`) to the React UI in real time.
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
│   │   │   └── routers/       # /api/ask, /api/jobs, /api/runs, /api/status
│   │   └── tests/
│   ├── mcp/                 # FastMCP analytical server (DuckDB + Parquet)
│   │   ├── mcp_server/      # Tools (query_taxi_data, get_dataset_profile)
│   │   └── tests/
│   └── dataset_spike/       # NYC TLC Yellow Taxi dataset profiling & spike
├── web/                     # React 18 + TypeScript + Vite UI
│   ├── src/                 # App, TimelineInspector, ContextInspector
│   └── dist/                # Production build artifacts for S3 deployment
├── infra/
│   └── terraform/           # Complete AWS infrastructure (ALB, ECS, S3, CloudFront, Route 53)
├── scripts/
│   ├── smoke/               # Milestone verification & integration smoke scripts
│   └── deploy_frontend.sh   # S3 sync + CloudFront cache invalidation
└── docs/
    ├── public-uat-guide.md  # Public cloud UAT acceptance testing guide
    ├── local-uat-guide.md   # Local docker UAT testing guide
    ├── work-history/        # Monotonic post-bootstrap ledger (0001–0025)
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

> 🧪 **Local Testing Runbook**: For a step-by-step local test checklist with command snippets and expected outputs, see the **[Local Docker UAT Guide](docs/local-uat-guide.md)**.

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
  - `ai-app` Task Role: Permissions restricted to `bedrock:InvokeModel` on `us.anthropic.claude-3-5-haiku-20241022-v1:0` and `amazon.nova-micro-v1:0`, `dynamodb:*` on the state table, and `s3:GetObject` on the artifacts bucket.
  - `analytics-mcp` Task Role: Zero AWS data permissions (operates strictly on local container storage).
- **Private S3 Bucket**: Public access blocked completely; accessible strictly by Amazon CloudFront via SigV4 Origin Access Control (OAC).

---

## Teardown & Resource Destruction

To destroy all provisioned AWS cloud resources and prevent ongoing charges:

```bash
terraform -chdir=infra/terraform destroy
```

---

## Frequently Asked Questions & System Concepts

### What dataset is being analyzed?
The analytical engine is pinned to the official **NYC TLC Yellow Taxi dataset (January 2024)**, consisting of 2,964,624 trip records and 265 official NYC taxi zone definitions. FastMCP runs memory-bounded DuckDB queries against the local Parquet data directly within the analytical container using zero-copy columnar views.

### Why is FastMCP separated from FastAPI?
Separating FastAPI and FastMCP creates a hard security and architectural boundary:
- **FastAPI (`ai-app`)** owns all LLM provider interactions, loop budgets, prompt templates, and durable state storage.
- **FastMCP (`analytics-mcp`)** exposes strictly allowlisted, read-only analytics tools (`query_taxi_data`, `get_dataset_profile`). It **never** receives arbitrary user SQL, never makes LLM calls, and never stores persistent user data.

### How does the system handle state and real-time streaming?
- **Durable State**: **Amazon DynamoDB** stores complete conversation threads, messages, execution runs, and job metadata.
- **Transient Streaming**: **Redis Streams** coordinate real-time Server-Sent Events (`run.received`, `step.tool_proposal`, `step.tool_execution`, `step.final_answer`, `run.completed`). If Redis is restarted, no durable conversation state is lost.

### How do Execution Budgets prevent runaway costs?
Every request operates under an immutable `ExecutionBudget` with strict limits:
- **Maximum Iterations**: 5 model turns
- **Maximum Tool Calls**: 3 invocations
- **Maximum Execution Time**: 30.0 seconds
- **Maximum Result Size**: 8,192 bytes (with automatic context reduction)

### What makes the AWS infrastructure cost-efficient?
- **Zero-NAT Architecture**: ECS Fargate containers use direct Internet Gateway routing with public IPs and strict security groups, saving ~$32+/month per NAT gateway.
- **Serverless Compute**: Fargate containers, DynamoDB on-demand billing, and CloudFront pay-per-request ensure costs remain near zero when idle.

---

## Work History & Decisions

- [Public Cloud UAT Acceptance Guide](docs/public-uat-guide.md)
- [Local Docker UAT Guide](docs/local-uat-guide.md)
- [Implementation Plan](docs/implementation-plan.md)
- [Monotonic Work History Ledger](docs/work-history/README.md)
- [Architectural Decisions](docs/decisions/README.md)
- [Progress Log](docs/progress.md)

# AI Analytics POC — Codex Requirements

## 1. Goal

Build a small but real AI analytics application that demonstrates:

- a separate FastMCP server
- an application-owned LLM orchestration loop
- MCP tool/resource discovery and tool calls
- durable conversation/run persistence
- Redis Streams for run-step/event coordination
- SSE from backend to browser
- bounded working context
- explicit loop/token/tool/cost budgets
- async task execution
- a real public analytical dataset queried with DuckDB
- a deployed UI that visibly shows each orchestration step

This is a learning/demo POC, not a production platform. Keep infrastructure deliberately small.

## 2. Non-goals

Do not build:

- a customer ingestion platform
- Kafka/Kinesis
- multi-region infrastructure
- Kubernetes
- elaborate CI/CD
- a full enterprise auth platform
- a managed Redis cluster
- a production secrets rotation system
- a vector database
- a general-purpose RAG framework
- a separate prompt-management product
- a warehouse such as Snowflake

The analytical dataset is local Parquet queried through DuckDB.

## 3. Reference deployment — AWS only

Use AWS for the deployed POC. Do not use Netlify, GCP, GKE, EKS, or a second hosting platform.

### Public entry point

The public URL is the CloudFront distribution hostname, for example:

```text
https://d123example.cloudfront.net
```

CloudFront does **not** provide a stable public IP and the POC does not require one.

Use one CloudFront distribution with two origins:

```text
Viewer / Browser
        |
        | HTTPS
        v
+--------------------------------------------+
| CloudFront                                |
| public hostname                           |
|                                            |
| default behavior /*  -------------------+ |
|                                           | |
| /api/* behavior ----------------------+  | |
+---------------------------------------|--|-+
                                        |  |
                             no cache   |  | cached static assets
                                        |  |
                                        v  v
                              +-----------+  +-------------------+
                              | Public ALB|  | Private S3 bucket |
                              | HTTPS :443|  | React build       |
                              +-----+-----+  +-------------------+
                                    |
                                    | HTTP :8080 inside VPC
                                    v
                 +--------------------------------------+
                 | ECS/Fargate service: ai-app         |
                 | desired count = 1                   |
                 |                                      |
                 | FastAPI orchestrator :8080          |
                 | async worker container              |
                 | Redis container :6379               |
                 +----------------+---------------------+
                                  |
                         private MCP traffic
                         ECS Service Connect
                                  |
                                  v
                 +--------------------------------------+
                 | ECS/Fargate service: analytics-mcp  |
                 | desired count = 1                   |
                 |                                      |
                 | FastMCP :8000                       |
                 | DuckDB                              |
                 | local Parquet dataset               |
                 +----------------+---------------------+
                                  |
                           startup download
                                  |
                                  v
                           NYC TLC dataset

ai-app
  |
  +------ HTTPS / AWS SDK ------> Amazon Bedrock
  |
  +------ AWS SDK --------------> DynamoDB
  |
  +------ AWS SDK --------------> S3 artifact bucket
  |
  +------ logs -----------------> CloudWatch Logs
```

### CloudFront routing

Configure these behaviors:

```text
/*                      -> private S3 frontend origin
/api/*                  -> ALB origin, caching disabled
/api/runs/*/events      -> ALB origin, caching disabled
```

For the API origin:

- forward required headers, query strings, and HTTP methods;
- disable caching;
- preserve streaming responses;
- do not configure a short response-completion timeout for the SSE path;
- FastAPI must emit periodic SSE heartbeat/progress events so the connection is not idle for longer than the configured origin read timeout.

This gives the React app and API one public hostname, so the frontend can call relative URLs such as:

```text
POST /api/conversations/...
GET  /api/runs/{run_id}/events
```

No browser CORS configuration is required when the UI and API are presented through the same CloudFront hostname.

### S3 frontend origin

Build the React application into static assets and upload them to a dedicated S3 bucket.

Use:

- private bucket;
- CloudFront Origin Access Control;
- no public S3 website endpoint;
- `index.html` as the SPA entry point;
- CloudFront SPA fallback for client-side routes if needed.

A deployment script may do:

```text
npm run build
aws s3 sync dist/ s3://<frontend-bucket>/ --delete
aws cloudfront create-invalidation --distribution-id <id> --paths "/*"
```

### ALB

Use one internet-facing Application Load Balancer because ECS/Fargate tasks are private VPC targets and CloudFront needs an HTTP origin.

POC configuration:

```text
ALB :443
    |
    v
Target Group
    |
    v
ai-app ECS service :8080
```

Do not create a public route to the MCP service.

The MCP service is reachable only from `ai-app` using ECS Service Connect/private service discovery.

For a learning POC, it is acceptable for the ALB to be publicly addressable even though the intended user entry point is CloudFront. A later hardening step could restrict direct ALB access to CloudFront.

### Why S3 + CloudFront instead of Netlify

Choose S3 + CloudFront because:

- the project intentionally demonstrates an AWS architecture;
- CloudFront serves both the static frontend and dynamic API through one hostname;
- relative `/api/*` calls avoid cross-origin browser configuration;
- the frontend is just static files, so S3 is sufficient;
- there is no separate deployment platform to explain in the architecture;
- CloudFront currently has a $0/month Free plan with usage allowances suitable for a small demo.

Netlify remains a valid alternative but is not part of the reference implementation.

If Netlify were used, it could still reach AWS normally:

```text
Browser loads JS from Netlify
        |
        +---- HTTPS ----> public AWS ALB/API
```

The browser, not Netlify hosting, makes the API request. The AWS API would need HTTPS and CORS allowing the Netlify domain.

### Why ECS/Fargate

Use ECS/Fargate rather than EKS/GKE because the learning goal is the AI application architecture, not Kubernetes administration.

Fargate provides:

- real containers;
- separate independently deployable app and MCP services;
- ECS task IAM roles;
- ALB integration;
- service discovery;
- per-task CPU/memory control;
- no EC2 node management.

Keep both services running continuously for the demo week rather than implementing wake-on-demand in v1.

### Deliberate POC task sizes

Start small:

```text
ai-app:
  0.5 vCPU
  1 GB memory
  desired_count = 1

analytics-mcp:
  0.5 vCPU
  1 GB memory
  desired_count = 1
```

Increase the MCP task memory only if DuckDB requires it for the selected dataset/query.

### Deliberate Redis limitation

Run Redis as a Docker sidecar in the `ai-app` ECS task.

This means:

```text
ai-app desired_count = 1
```

for the POC.

Redis is real and is used for Redis Streams, but it is not a production distributed cache.

Production evolution:

```text
Redis sidecar
    ->
shared ElastiCache / another shared coordination system
```

### AWS state services

Use:

```text
DynamoDB
- conversations
- messages
- runs
- run steps
- job metadata

S3
- static React build
- optional generated result artifacts

CloudWatch Logs
- ai-app logs
- MCP logs

Amazon Bedrock
- on-demand LLM inference

ECS Service Connect
- private app -> MCP connectivity
```

Do not add RDS, EFS, Kafka, OpenSearch, EKS, or a service mesh to the first version.

## 5. Dataset

Use one month of the public NYC Taxi & Limousine Commission Yellow Taxi trip-record dataset in Parquet format.

At MCP service startup:

1. Check whether the configured Parquet file exists in local ephemeral storage.
2. If absent, download it from the configured dataset URL.
3. Initialize DuckDB views over the Parquet file.
4. Load a small taxi-zone lookup file if needed.
5. Expose only an allowlisted analytical schema through MCP.

Suggested columns:

- pickup datetime
- dropoff datetime
- pickup zone
- dropoff zone
- passenger count
- trip distance
- fare amount
- tip amount
- toll amount
- total amount
- payment type

Suggested starter questions:

- What hours of day have the most taxi trips?
- Which pickup zones generate the most revenue?
- Which pickup/dropoff pairs are most common?
- How does average tip percentage vary by payment type?
- Which trips look unusually expensive for their distance?
- How does trip volume change by weekday?
- What are the busiest zones during evening hours?
- Compare average fare and trip distance across the top pickup zones.

## 6. Frontend

Deploy a small React application as static assets in a private S3 bucket served through CloudFront.

The page contains:

- conversation history
- prompt input
- suggested starter questions
- streaming assistant answer
- run status
- step timeline
- cost/budget summary
- final data table/chart

### Step timeline

Visibly render events such as:

```text
Run received
Loading conversation
Discovering MCP capabilities
Loading semantic/schema context
Calling LLM
LLM requested tool: query_taxi_data
Validating tool call
Executing MCP tool
DuckDB query completed
Persisting observation
Calling LLM with bounded result
Generating final answer
Completed
```

## 7. Browser API

### Start or continue conversation

```http
POST /api/conversations/{conversation_id}/messages
Content-Type: application/json
Idempotency-Key: <uuid>

{
  "text": "Which pickup zones generate the most revenue?"
}
```

Response:

```json
{
  "conversation_id": "conv_...",
  "message_id": "msg_...",
  "run_id": "run_...",
  "status": "RECEIVED",
  "events_url": "/api/runs/run_.../events"
}
```

### SSE

```http
GET /api/runs/{run_id}/events
Accept: text/event-stream
```

Example events:

```text
event: run.status
data: {"run_id":"run_1","status":"CONTEXT_LOADING"}

event: step.started
data: {"step":"mcp.query","tool":"query_taxi_data"}

event: step.completed
data: {"step":"mcp.query","row_count":10,"duration_ms":132}

event: usage
data: {"input_tokens":2100,"output_tokens":320,"estimated_cost_usd":0.0031}

event: answer.delta
data: {"text":"The highest-revenue pickup zones..."}

event: run.completed
data: {"run_id":"run_1"}
```

## 8. IDs

Keep these concepts separate:

```text
conversation_id
message_id
run_id
step_id
llm_call_id
tool_call_id
mcp_request_id
mcp_session_id
query_id
job_id
```

Application conversation state must never depend on an MCP session.

## 9. Durable state

Use DynamoDB in the AWS deployment.

Suggested logical entities:

```text
Conversation
- conversation_id
- created_at
- updated_at

Message
- message_id
- conversation_id
- role
- content
- created_at

Run
- run_id
- conversation_id
- message_id
- status
- model
- prompt_version
- started_at
- completed_at
- input_tokens
- output_tokens
- estimated_cost_usd
- failure_code

RunStep
- step_id
- run_id
- sequence
- step_type
- status
- tool_name
- input_summary
- output_summary
- started_at
- completed_at
- duration_ms
```

Do not put large query results in DynamoDB.

## 10. Redis

Run Redis as a Docker container inside the `ai-app` ECS task.

Use Redis Streams to demonstrate coordination.

Streams:

```text
run-events
async-jobs
```

Redis may contain:

- run progress
- SSE fan-out events
- cancellation flags
- temporary idempotency markers
- hot conversation summaries
- tool-definition cache
- async job queue for the demo

Redis is not the durable source of truth.

## 11. MCP server

Implement with Python + FastMCP.

The MCP server must not call the LLM.

### Startup

Demonstrate/record:

```text
initialize
notifications/initialized
tools/list
resources/list
resources/read
```

### MCP resources

```text
dataset://nyc-taxi/schema
dataset://nyc-taxi/business-rules
dataset://nyc-taxi/sample-questions
```

### MCP tools

Start with:

```text
query_taxi_data
get_dataset_profile
```

POC SQL rules:

- SELECT only
- exactly one statement
- allowlisted views/tables
- hard row limit
- execution timeout
- no filesystem/network functions
- result byte limit

Return:

- columns
- bounded rows
- row count
- execution duration
- query ID
- truncated flag

## 12. LLM orchestration

The Application Server owns the loop.

```text
load durable run state
        |
        v
build bounded WorkingContext
        |
        v
call LLM
        |
  +-----+----------------+
  |                      |
final answer          tool call
  |                      |
validate final       validate tool request
  |                      |
complete             call MCP
                         |
                         v
                  persist observation
                         |
                         v
                  reduce working context
                         |
                         +------> loop
```

The LLM never calls MCP directly.
The MCP server never calls the LLM.

## 13. Working context

Maintain an immutable event history plus a bounded working context.

```python
WorkingContext:
    conversation_summary
    current_user_message
    recent_messages
    available_tools
    dataset_schema
    recent_tool_observations
    assumptions
    artifacts
    failures
    remaining_budget
```

Context reducer rules:

- keep current user request verbatim
- keep recent turns
- summarize old conversation
- deduplicate schema
- preserve unresolved constraints
- preserve tool failures
- replace large results with schema + aggregates + preview + artifact reference

## 14. Run budgets

Suggested initial POC configuration:

```text
MAX_ITERATIONS=6
MAX_LLM_CALLS=6
MAX_TOOL_CALLS=8
MAX_RUN_SECONDS=60
MAX_INPUT_TOKENS_PER_RUN=30000
MAX_OUTPUT_TOKENS_PER_RUN=8000
MAX_ESTIMATED_COST_USD=0.10
MAX_QUERY_ROWS=200
MAX_TOOL_RESULT_BYTES=262144
```

Also stop when:

- the same tool call repeats with equivalent arguments
- the user cancels
- the model produces an invalid action repeatedly
- a dependency exceeds the run deadline

## 15. Async job example

Support one deliberately asynchronous operation:

```text
"Generate a detailed daily-zone report for the entire dataset."
```

Flow:

```text
LLM proposes create_report
        |
        v
App validates
        |
        v
create job_id
persist status=PENDING
        |
        v
Redis Stream: async-jobs
        |
        v
worker consumes job
        |
        v
DuckDB report query
        |
        v
artifact created
        |
        v
job status=COMPLETED
        |
        v
SSE run/job event
```

## 16. LLM

AWS deployment default:

```text
Amazon Bedrock
```

Use on-demand inference.

Configuration:

```text
LLM_PROVIDER=bedrock
LLM_MODEL_ID=...
```

Use the ECS task IAM role for Bedrock access rather than static AWS access keys.

## 17. AWS identity, secrets, and security scope

### Principle

Do not create long-lived AWS access keys for the application.

Use IAM roles for all AWS-native service access.

There are two distinct ECS roles:

```text
ECS TASK ROLE
  Used by application code running inside the container.

  ai-app permissions:
    - Bedrock model invocation
    - DynamoDB read/write for app state
    - S3 read/write for result artifacts

  analytics-mcp permissions:
    - ideally none
    - optionally S3 read if the demo dataset is moved into a private S3 bucket


ECS TASK EXECUTION ROLE
  Used by the ECS/Fargate platform, not by application business logic.

  permissions:
    - pull container images from ECR
    - write task logs to CloudWatch
    - retrieve a Secrets Manager/SSM value only if the task definition
      explicitly injects a secret
```

The AWS SDK inside the container should use its normal/default credential provider chain.
On ECS/Fargate it should automatically obtain temporary credentials from the task role.

Never configure:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
```

as application environment variables for the deployed service.

### Which components need secrets?

For the initial AWS-only implementation, almost everything can avoid application secrets:

```text
Bedrock
  -> IAM task role
  -> no API key

DynamoDB
  -> IAM task role
  -> no username/password

S3 artifacts
  -> IAM task role
  -> no access key

S3 frontend
  -> CloudFront Origin Access Control
  -> no secret in React application

CloudWatch
  -> ECS execution/task permissions
  -> no application secret

ECR image pulls
  -> ECS task execution role
  -> no application secret

NYC TLC public dataset
  -> public HTTPS download
  -> no secret

Redis sidecar
  -> task-internal POC dependency
  -> do not expose Redis publicly
  -> no password required for v1

ai-app -> analytics-mcp
  -> private ECS Service Connect/security-group path
  -> no shared API key in v1
```

The lack of an application-layer credential between `ai-app` and `analytics-mcp`
is a deliberate POC simplification. In a production multi-tenant platform, use
strong workload/service identity and authorization at this boundary.

### When Secrets Manager becomes necessary

Add AWS Secrets Manager only if the project introduces a credential that IAM
cannot replace, for example:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
third-party SaaS API token
external database username/password
customer API credential
private non-AWS service token
```

Then:

```text
Secrets Manager
      |
      | ECS task-definition `secrets`
      v
container environment/runtime
```

Do not put the secret value into:

```text
terraform.tfvars
Terraform source
GitHub repository
Dockerfile
ECS plain `environment` entries
frontend JavaScript
README
```

Terraform should manage only the secret **container/ARN/reference** if needed.
Populate the actual secret value separately, unless there is a strong reason
for Terraform state to contain the secret.

### Non-secret configuration

Normal configuration belongs in ECS task-definition environment variables:

```text
ENVIRONMENT=demo
AWS_REGION=...
LLM_PROVIDER=bedrock
LLM_MODEL_ID=...
DYNAMODB_TABLE_NAME=...
ARTIFACT_BUCKET_NAME=...
MCP_BASE_URL=...
REDIS_URL=redis://localhost:6379
DATASET_URL=...
MAX_ITERATIONS=6
MAX_TOOL_CALLS=8
MAX_RUN_SECONDS=60
MAX_ESTIMATED_COST_USD=0.10
```

These values are configuration, not secrets.

### Public demo protection

Do not build full authentication for the POC.

Protect cost and abuse using application-level controls:

- strict per-run LLM/tool/token/dollar budgets;
- modest per-IP/request throttling in FastAPI;
- hard maximum concurrent runs;
- read-only MCP tools;
- no arbitrary network/file access through DuckDB;
- ability to disable the ECS service after the demo period.

Do not place any sensitive or private customer data in the POC.

## 18. Observability

Structured logs include when available:

```text
trace_id
conversation_id
message_id
run_id
step_id
llm_call_id
tool_call_id
query_id
job_id
```

Expose a developer/debug view showing:

- stage latency
- LLM call count
- tool-call count
- input/output tokens
- estimated run cost
- context size
- loop iteration
- final stop reason

## 19. Terraform deployment

Terraform is part of the POC and should provision the AWS infrastructure.
Application deployment should remain simple and script-driven.

### Terraform owns

```text
Networking
- VPC
- public subnets
- ECS task subnets
- security groups

Container platform
- ECS cluster
- ECR repositories
- ai-app task definition + service
- analytics-mcp task definition + service
- ECS Service Connect/private discovery

Public API
- internet-facing ALB
- HTTPS listener when a certificate/domain is configured
- HTTP listener is acceptable for an initial temporary demo only when
  CloudFront is the public HTTPS entry point and the origin configuration is safe
- target group for ai-app

Frontend
- private S3 frontend bucket
- CloudFront Origin Access Control
- CloudFront distribution
- default behavior -> S3
- `/api/*` behavior -> ALB

State/storage
- DynamoDB application-state table(s)
- S3 artifact bucket

Identity
- ECS task execution role
- ai-app task role
- analytics-mcp task role
- least-privilege IAM policies

Operations
- CloudWatch log groups
```

Terraform does **not** need to:

```text
build React
upload frontend files on every code change
build Docker images
push Docker images
populate real secret values
run database migrations
```

Those belong in small deployment scripts.

### Suggested Terraform layout

Keep this flat and easy to understand rather than building a large module hierarchy:

```text
infra/terraform/
├── versions.tf
├── providers.tf
├── variables.tf
├── locals.tf
├── network.tf
├── iam.tf
├── ecr.tf
├── s3.tf
├── dynamodb.tf
├── ecs.tf
├── alb.tf
├── cloudfront.tf
├── outputs.tf
└── terraform.tfvars.example
```

### Important IAM design in Terraform

Create separate roles:

```text
ecs_task_execution_role
  -> ECR pull
  -> CloudWatch logs
  -> Secrets Manager read only if a secret is actually configured


ai_app_task_role
  -> Bedrock invoke only for configured model(s)
  -> DynamoDB access only to application state table(s)
  -> S3 access only to artifact bucket/prefix


analytics_mcp_task_role
  -> no AWS data permissions by default
  -> optional read-only S3 permission if dataset is hosted privately in S3
```

Do not use `AdministratorAccess` for any ECS task role.

### Terraform state

For the first local deployment:

```text
terraform init
terraform plan
terraform apply
```

Local Terraform state is acceptable for a single-developer one-week POC as long
as it is excluded from Git.

Add to `.gitignore`:

```text
.terraform/
*.tfstate
*.tfstate.*
*.tfvars
```

Commit:

```text
terraform.tfvars.example
```

with only example/non-secret values.

If the project later adds automated CI deployment or multiple collaborators,
move Terraform state to an S3 backend with state locking.

### Build/deploy flow

Provide scripts or Make targets:

```text
make infra
    terraform init
    terraform apply

make images
    docker build app
    docker build mcp
    push both images to ECR

make deploy-backend
    update ECS services/task definitions to new image tags
    wait for healthy services

make deploy-frontend
    npm ci
    npm run build
    aws s3 sync frontend/dist/ s3://<frontend-bucket>/ --delete
    aws cloudfront create-invalidation --distribution-id <id> --paths "/*"

make deploy
    images
    deploy-backend
    deploy-frontend
```

Terraform outputs should expose machine-readable values needed by the scripts:

```text
ecr_ai_app_repository_url
ecr_mcp_repository_url
frontend_bucket_name
artifact_bucket_name
cloudfront_distribution_id
cloudfront_domain_name
ecs_cluster_name
ai_app_service_name
mcp_service_name
```

### Docker image references

Do not hard-code mutable `latest` as the only deployment identity.

Use a build identifier such as:

```text
git commit SHA
```

Example:

```text
<account>.dkr.ecr.<region>.amazonaws.com/ai-app:<git-sha>
<account>.dkr.ecr.<region>.amazonaws.com/analytics-mcp:<git-sha>
```

Terraform may create the services initially with a bootstrap image/tag.
Subsequent application deploy scripts can register/update task-definition
revisions using the new immutable image tag.

This keeps infrastructure provisioning separate from application release.

### Network simplification

Avoid adding NAT Gateway solely to imitate production.

The POC needs outbound access for:

- Bedrock;
- DynamoDB;
- S3;
- ECR/CloudWatch during task startup/runtime;
- public NYC TLC dataset download.

Codex should choose the simplest inexpensive network layout that supports these
dependencies and document the trade-off.

Do not add a large collection of VPC endpoints merely to make the architecture
look more production-like.

### Destroy

Provide:

```text
make destroy
```

which runs:

```text
terraform destroy
```

after first emptying versioned/non-empty buckets if required.

The README must prominently remind the user to destroy the environment when the
demo week is over.

## 20. Codex harness execution plan

This project must be implemented **incrementally**.

The coding agent must NOT scaffold or implement the complete architecture in one pass.

The parent Codex agent owns the active milestone. It may delegate bounded research,
inspection, testing, or review work to subagents, but it should keep write ownership
for the current milestone centralized unless two write tasks are clearly independent.

### Core execution rule

At the start of every milestone, the parent agent must:

```text
1. Read the milestone goal.
2. Inspect the current repository state.
3. State the smallest implementation required.
4. Delegate only bounded independent research/review tasks.
5. Implement only this milestone.
6. Run tests/verification.
7. Update README/progress documentation.
8. Stop.
```

Do not automatically continue into the next milestone.

Each milestone ends with:

```text
DONE WHEN
- implementation works
- tests/checks pass
- a human-verifiable demo command exists
- README/progress notes are updated
- no later milestone has been prematurely implemented
```

The user should be able to review the result after every milestone.

### Repository guidance for Codex

Create a root-level `AGENTS.md`.

Keep it short and persistent.

Suggested content:

```md
# Project execution rules

Build this project milestone by milestone.

- Read `docs/implementation-plan.md` before making changes.
- Work on only the currently requested milestone.
- Do not pre-build future architecture.
- Prefer the smallest working vertical slice.
- Every milestone must end with tests or a manual verification command.
- Keep FastAPI orchestration and FastMCP as separate services once MCP is introduced.
- The application server owns all LLM calls.
- The MCP server never calls the LLM.
- Redis is transient coordination, never durable conversation state.
- Do not add infrastructure unless the current milestone requires it.
- Do not add Kubernetes, Kafka, RDS, EFS, OpenSearch, or a vector database.
- Never create long-lived AWS credentials.
- Before marking a milestone complete, run the milestone acceptance checks.
- After corrections from the user, update AGENTS.md only if the correction is a
  reusable repository rule.
```

### Subagent philosophy

Use subagents primarily for:

```text
research
codebase exploration
test design
review
security review
AWS/Terraform review
dataset inspection
UI review
failure analysis
```

Do not use multiple subagents to simultaneously rewrite tightly coupled files.

The parent agent must collect concise subagent findings and make the final implementation
decision.

### Suggested reusable subagent roles

These are logical roles. They do not all need to run on every milestone.

#### 1. Dataset scout

Purpose:

```text
Find and validate the exact public NYC TLC dataset artifact needed by the POC.
```

Responsibilities:

- use the official NYC TLC Trip Record Data source;
- choose one fixed Yellow Taxi Parquet month;
- record the official source page and exact artifact URL in `config/demo-data.yaml`
  or equivalent;
- inspect file size and schema;
- identify whether Taxi Zone lookup data is required;
- propose 5-10 deterministic demo questions;
- return findings to the parent agent;
- do not modify application architecture.

Output:

```text
dataset URL
dataset month/version
file size
schema summary
lookup-file requirement
sample analytical questions
download verification command
```

Prefer pinning a stable historical month rather than “latest” so the demo remains reproducible.

#### 2. MCP protocol reviewer

Purpose:

```text
Inspect the FastMCP implementation for correct ownership and protocol boundaries.
```

Check:

- MCP server is a separate process/service;
- initialize/discovery works;
- tools/resources are discoverable;
- app calls MCP; MCP does not call LLM;
- tool inputs/outputs are typed and bounded;
- no arbitrary unsafe DuckDB access is exposed.

Return findings only unless explicitly assigned a fix.

#### 3. LLM orchestration reviewer

Purpose:

```text
Review the application-owned agent loop.
```

Check:

- loop owned by FastAPI/application layer;
- tool proposal -> validation -> MCP execution -> observation -> next LLM call;
- explicit stop conditions;
- cost/token/tool/time budgets;
- repeated-tool detection;
- bounded working context;
- persistence around external side effects.

#### 4. Persistence/state reviewer

Purpose:

```text
Check IDs, state ownership, and recovery semantics.
```

Check:

- conversation_id != run_id != step_id != MCP session;
- durable data stored in DynamoDB;
- Redis contains only transient coordination;
- app restart can reconstruct conversation/run state;
- large results are not stored in DynamoDB/Redis.

#### 5. Frontend UX reviewer

Purpose:

```text
Review whether the UI visibly demonstrates the architecture.
```

Check:

- simple conversation UI;
- suggested questions;
- SSE status;
- orchestration step timeline;
- token/tool/cost counters;
- error/budget-exceeded states;
- no hidden implementation magic required to understand the demo.

#### 6. Terraform/AWS reviewer

Purpose:

```text
Review the smallest deployable AWS architecture.
```

Check:

- S3 + CloudFront frontend;
- `/api/*` -> ALB -> ECS;
- separate app and MCP ECS services;
- correct task role vs execution role;
- no static AWS keys;
- IAM least privilege;
- Terraform outputs useful to deploy scripts;
- no unnecessary NAT/Kubernetes/production infrastructure.

#### 7. Test/release reviewer

Purpose:

```text
Run the milestone acceptance criteria from a clean perspective.
```

Check:

- unit tests;
- integration tests where applicable;
- Docker Compose startup;
- health endpoints;
- exact curl/browser verification;
- no regression in previous milestones.

This agent should report failures and reproduction commands rather than silently changing
large parts of the implementation.

---

## Repository bootstrap — before Milestone 0

Set up GitHub before writing application functionality.

This is the **only** time a direct push to `main` is allowed.

### Bootstrap sequence

```text
1. Create empty local repository.
2. Create GitHub repository.
3. Add only project governance/skeleton files.
4. Make initial commit on main.
5. Push main once.
6. Immediately protect main.
7. All later changes go through PRs.
```

Suggested first commit:

```text
chore(repo): bootstrap project
```

It may contain only:

```text
README.md
AGENTS.md
.gitignore
.editorconfig
pyproject.toml
Makefile
docs/implementation-plan.md
docs/progress.md
docs/work-history/README.md
docs/decisions/README.md
terraform.tfvars.example
```

Do **not** put `/health` in this bootstrap commit.
That becomes PR #1.

The bootstrap phase is historical. The canonical requirements source
`ai_analytics_poc_requirements_aws_v5.md` is tracked after bootstrap and changes to it follow
the normal branch and pull-request workflow.

### GitHub repository creation

Example:

```bash
git init -b main
git add .
git commit -m "chore(repo): bootstrap project"

gh repo create <repo-name> \
  --private-or-public-as-requested \
  --source=. \
  --remote=origin \
  --push
```

After the first push, configure the `main` ruleset/branch protection:

```text
require pull request before merging
do not allow direct feature pushes
no approval requirement for a solo POC
require conversations resolved when practical
add required status checks later when CI exists
```

The project is intentionally solo-friendly: requiring a PR is valuable for history,
but requiring another human reviewer would unnecessarily block development.

After bootstrap:

```text
NO direct commits/pushes to main.
NO emergency "tiny fix" directly on main.
Every change gets a branch + PR.
```

---

## Python/FastAPI engineering style

### Starter shape

Use a small production-shaped FastAPI project from the first functional PR.

Do not start with a 300-line `main.py`, but also do not invent a large framework.

Initial shape:

```text
services/app/
├── pyproject.toml
├── Dockerfile
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   └── routers/
│       ├── __init__.py
│       └── health.py
└── tests/
    └── test_health.py
```

At Milestone 0 this still exposes only:

```http
GET /health
```

The structure exists so later routers/services can be added without rewriting the entry point.

### FastAPI conventions

Use:

```text
FastAPI application factory or clear application initialization
APIRouter for endpoints
Pydantic models for request/response boundaries
Pydantic Settings or equivalent for configuration
lifespan hooks for service startup/shutdown when needed
dependency injection only where it materially improves testing/boundaries
```

Do not add repositories, service layers, factories, or interfaces before a milestone
actually needs them.

### Python formatting

Use **Black** as the formatter.

```text
Black line length: default 88
```

Use Ruff for linting/import hygiene.

Suggested checks:

```bash
black --check services/
ruff check services/
pytest
```

For local autofix:

```bash
black services/
ruff check --fix services/
```

Do not run both Black and Ruff formatter. Black owns formatting; Ruff owns linting.

### Comment style

Comments should explain **why**, invariants, or non-obvious trade-offs.

Avoid comments that simply narrate the next line.

Preferred comment length:

```text
roughly 40-60 characters where practical
```

This is a readability preference, **not** a hard linter rule.

Examples:

Good:

```python
# Bound results before they enter model context.
rows = rows[:max_rows]

# MCP state must never identify a conversation.
mcp_session_id = response.session_id
```

Bad:

```python
# Set max rows.
max_rows = 100

# Call function.
result = call_tool()
```

Longer explanations belong in:

```text
docstrings
ADRs
work-history
README
```

not large inline comment blocks.

### Naming/style rules

Prefer:

```text
descriptive names
small functions
typed public boundaries
explicit return types for non-trivial functions
async only for actual I/O/concurrency
dependency injection for replaceable external clients
```

Avoid:

```text
"utils.py" dumping grounds
"manager" classes with many responsibilities
global mutable state
premature abstract base classes
generic BaseService/BaseRepository patterns
```

### Testing style

Tests should focus on externally meaningful behavior.

Early:

```text
health endpoint returns 200
MCP ping succeeds
dataset profile returns expected shape
fake LLM produces controlled tool proposal
```

Later:

```text
loop stops on budget
conversation survives restart
Redis loss does not lose durable state
SSE emits expected step sequence
```

Every bug fix should add the smallest regression test or smoke check that would have
caught the bug when practical.

---

## Authentication decision — deliberately omitted

Do **not** implement end-user authentication in the first POC.

No:

```text
Google login
Gmail OAuth
Cognito user pool
password accounts
enterprise SSO
user profile system
```

Reason:

```text
The project is demonstrating AI orchestration,
MCP boundaries, persistence, streaming, context,
budgets, and AWS deployment.

Authentication would add substantial unrelated
surface area without improving those lessons.
```

Use one synthetic demo identity in application code/config:

```text
tenant_id = "demo"
user_id   = "public-demo"
```

Keep identity creation in one clearly named boundary so real authentication could be
inserted later without rewriting conversation/run ownership.

Example:

```python
def current_demo_principal() -> Principal:
    return Principal(
        tenant_id="demo",
        user_id="public-demo",
    )
```

Do not pretend this is production authentication.

README must explicitly say:

```text
Authentication is intentionally out of scope.
The public deployment contains only a public dataset
and read-only analytical tools.
```

### Why not Google login?

Google login is a reasonable future milestone if the demo must be restricted to named users,
but it would introduce:

```text
OAuth client setup
redirect URIs
session/cookie handling
additional secrets
identity persistence
logout/expiry behavior
```

None of that helps validate the core AI architecture.

If public abuse becomes a problem, first disable the demo or add a simple deployment-level
access mechanism rather than expanding the project into an identity system.

---

## Bedrock public-demo cost controls

The public POC must assume that anonymous users may repeatedly invoke the API.

Do not rely on AWS Budgets alone to prevent spend.

Use **three layers**.

### Layer 1 — per-run hard budget

Default:

```text
MAX_ESTIMATED_COST_USD_PER_RUN=0.10
MAX_LLM_CALLS=6
MAX_TOOL_CALLS=8
MAX_RUN_SECONDS=60
MAX_OUTPUT_TOKENS_PER_RUN=8000
```

These remain configurable because exact model pricing differs.

### Layer 2 — application-enforced global demo allowance

Default hosted allowance:

```text
DEMO_GLOBAL_LLM_BUDGET_USD=7.50
```

Acceptable configured range:

```text
$5-$10
```

This is the **runtime admission gate**.

Store one durable budget record in DynamoDB:

```text
DemoBudget
- budget_id = "hosted-demo"
- limit_usd
- reserved_usd
- consumed_usd
- updated_at
```

Before every Bedrock inference:

```text
1. estimate maximum cost of this call;
2. atomically reserve that amount;
3. reject if reservation would exceed limit;
4. invoke Bedrock;
5. read returned usage/token counts;
6. calculate observed estimated cost;
7. convert reservation to consumed amount;
8. release unused reservation.
```

Use a DynamoDB conditional update/transaction so concurrent requests cannot all pass a
non-atomic budget check.

If the global budget is exhausted:

```http
HTTP 429 or 503

{
  "error": "demo_budget_exhausted",
  "message": "The hosted AI demo budget has been used."
}
```

The static site and non-LLM dataset/profile views should remain available.

### Pricing configuration

Do not bury changing model prices in orchestration logic.

Configure:

```text
BEDROCK_INPUT_USD_PER_MILLION_TOKENS
BEDROCK_OUTPUT_USD_PER_MILLION_TOKENS
```

for the selected demo model.

Record the model ID with every run.

Bedrock usage returned from the inference API should be used to record input/output token
usage after each call.

When useful, Bedrock `CountTokens` may be used before inference to estimate input tokens,
but it is not required for the earliest milestone.

### Layer 3 — AWS account budget alerts

Terraform should optionally create an AWS Budget for the demo account/project with alerts,
for example:

```text
$5 actual spend alert
$8 actual spend alert
$10 actual/forecast alert
```

Notification destination can be configured by the developer.

AWS Budgets is a **billing/alerting safety net**, not the request-by-request hard stop.

The app-side `$7.50` allowance is specifically for Bedrock inference admission.

### Anonymous abuse controls

Also configure conservative controls:

```text
MAX_CONCURRENT_RUNS=2
MAX_RUNS_PER_IP_PER_HOUR=5
MAX_REQUEST_BODY_BYTES=<small limit>
```

Rate limiting may use Redis.

These controls are deterrence/containment, not identity.

Never send user-controlled arbitrary SQL directly to DuckDB.

The MCP query tool remains read-only and bounded.

### Hosted demo switch

Provide:

```text
DEMO_LLM_ENABLED=true|false
```

If false:

- UI still loads;
- dataset/profile examples still work;
- AI submit button says hosted LLM is disabled;
- no Bedrock invocation occurs.

This provides a zero-risk way to leave the repository/demo shell online after the active
review period.


# Phased implementation — every functional change is a PR

## Milestone 0 — FastAPI health endpoint (PR #1)

### Goal

Create the first functional application change after repository bootstrap.

Build only the minimal FastAPI service using the agreed project structure.

No MCP.
No React.
No Redis.
No AWS.
No LLM.

### Application

Create one FastAPI service with:

```http
GET /health
```

Response:

```json
{
  "status": "ok",
  "service": "ai-app"
}
```

### Done when

```text
make dev
curl localhost:<port>/health
pytest
```

all work.

Stop after this milestone.

---

## Milestone 1 — Empty MCP service

### Goal

Establish the **service boundary before adding intelligence**.

Add:

```text
services/mcp/
```

with FastMCP.

MCP has no DuckDB and no dataset yet.

Expose one trivial capability such as:

```text
tool: ping()
resource: app://about
```

The app service must connect to MCP and prove discovery/call flow.

Example app endpoint:

```http
GET /debug/mcp
```

returns discovered tools/resources and/or a successful ping result.

### Architecture at this milestone

```text
curl
  |
  v
FastAPI
  |
  | MCP
  v
FastMCP
```

No LLM.

### Subagents

Good delegation:

```text
MCP protocol reviewer
```

No other agent required.

### Done when

- both services start with Docker Compose;
- FastAPI can reach FastMCP;
- discovery succeeds;
- ping tool call succeeds;
- MCP has zero LLM code.

Stop.

---

## Milestone 2 — Dataset spike, isolated from AI

### Goal

Prove the data layer independently.

The Dataset Scout should first validate and pin one official NYC TLC Yellow Taxi
Parquet artifact.

Store the chosen metadata in repository configuration, for example:

```yaml
dataset:
  name: nyc-yellow-taxi
  year: <fixed year>
  month: <fixed month>
  parquet_url: <official pinned URL>
```

The official NYC TLC Trip Record Data page is the source of truth for the pinned file.

Then MCP startup:

```text
if local parquet missing:
    download pinned file

initialize DuckDB
create allowlisted view
```

Add MCP resource:

```text
dataset://nyc-taxi/schema
```

Add MCP tool:

```text
get_dataset_profile()
```

No natural-language SQL yet.

### Done when

- clean startup downloads or reuses the Parquet file;
- DuckDB opens it;
- schema resource works;
- profile tool returns row count/basic dataset information;
- one deterministic DuckDB query is covered by a test.

Stop.

---

## Milestone 3 — Minimal React UI

### Goal

Introduce the browser without AI complexity.

Build a small React application.

Screen contains:

```text
title
backend health
MCP health/discovery status
placeholder prompt input
placeholder timeline
```

The browser calls the FastAPI health/debug endpoints.

No LLM.
No chat persistence.
No SSE yet.

### Subagents

Optional:

```text
Frontend UX reviewer
```

after implementation.

### Done when

```text
docker compose up
```

provides:

```text
React -> FastAPI -> FastMCP
```

and the browser visibly shows both backend and MCP health.

Stop.

---

## Milestone 4 — First real LLM call, no tools

### Goal

Add Amazon Bedrock while preserving ownership.

FastAPI receives:

```http
POST /api/ask
```

with text.

FastAPI calls Bedrock once and returns the answer.

Architecture:

```text
Browser
   |
   v
FastAPI
   |
   v
Bedrock
```

MCP is not involved in this request yet.

Implement an `LLMClient` abstraction.

Track:

```text
llm_call_id
model_id
input tokens if available
output tokens if available
latency
estimated cost if deterministically calculable
```

### Critical invariant

```text
Only ai-app has Bedrock IAM permission.
MCP has no Bedrock permission and no LLM client.
```

### Done when

- real Bedrock call succeeds;
- model ID comes from configuration;
- no AWS static key exists;
- a unit test can use a fake LLM client.

Stop.

---

## Milestone 5 — One-turn LLM -> MCP tool execution

### Goal

Connect reasoning to governed tools for the first time.

Start with a **single iteration**.

Available LLM tool:

```text
get_dataset_profile
```

Flow:

```text
question
  |
  v
FastAPI
  |
  v
LLM
  |
  | proposes tool
  v
validate
  |
  v
MCP
  |
  v
DuckDB
  |
  v
tool result
  |
  v
LLM second call
  |
  v
final response
```

Do not create a generic unbounded loop yet.

### Done when

A real question causes:

```text
LLM call #1
MCP tool call
LLM call #2
final answer
```

and logs clearly show the sequence.

Stop.

---

## Milestone 6 — Governed analytical query tool

### Goal

Turn the demo into actual AI analytics.

Add:

```text
query_taxi_data
```

The preferred model contract is a constrained structured analytical request when practical.
If SQL is used, validate it deterministically.

Minimum controls:

```text
SELECT only
one statement
allowlisted views
row limit
result byte limit
execution timeout
block arbitrary filesystem/network functions
```

Add dataset/schema context to LLM input.

Test with 3-5 starter questions.

### Suggested first questions

Use deterministic questions selected during the Dataset Scout milestone, for example:

```text
Which pickup zones have the most trips?
What hours have the highest trip volume?
How does average trip distance vary by weekday?
```

### Done when

Each selected question:

```text
question -> LLM -> validated MCP query -> DuckDB -> bounded result -> answer
```

works reliably.

Stop.

---

## Milestone 7 — Durable conversation and run model

### Goal

Add persistence **after the execution path already works**.

Implement DynamoDB-backed:

```text
Conversation
Message
Run
RunStep
```

Add IDs:

```text
conversation_id
message_id
run_id
step_id
llm_call_id
tool_call_id
query_id
```

### Test the failure lesson explicitly

1. Create conversation.
2. Ask a question.
3. Restart the application container.
4. Continue the same conversation.
5. Verify prior messages/run metadata are reconstructed from durable storage.

Do not add Redis yet.

### Subagent

Run:

```text
Persistence/state reviewer
```

### Done when

application state survives process restart.

Stop.

---

## Milestone 8 — Real bounded orchestration loop

### Goal

Replace the hard-coded two-call sequence with the real agent loop.

Implement:

```text
while within budgets:
    build bounded context
    call LLM

    final?
        persist + complete

    tool call?
        validate
        execute MCP
        persist observation
        reduce context
        continue
```

Hard limits:

```text
max iterations
max LLM calls
max tool calls
deadline
input/output token budget
estimated dollar budget
tool-result byte limit
repeated-equivalent-tool-call detection
```

Add explicit terminal state:

```text
BUDGET_EXCEEDED
```

### Subagent

Run:

```text
LLM orchestration reviewer
```

### Done when

tests demonstrate:

```text
normal completion
multiple tool iterations
max-iteration termination
repeated-tool termination
budget termination
invalid-tool rejection
```

Stop.

---

## Milestone 9 — Redis Streams + SSE

### Goal

Make internal execution visible to the browser.

Add Redis Docker sidecar.

Use a Redis Stream such as:

```text
run-events
```

Publish:

```text
run.received
context.loading
llm.started
llm.completed
tool.requested
tool.started
tool.completed
context.reduced
run.completed
run.failed
run.budget_exceeded
```

FastAPI exposes:

```http
GET /api/runs/{run_id}/events
```

using SSE.

React consumes SSE and renders the timeline.

### Key storage distinction

```text
DynamoDB = durable source of truth
Redis    = transient execution/event coordination
```

The app must remain correct if Redis history disappears.

### Done when

the browser visibly animates a real multi-step run.

Stop.

---

## Milestone 10 — Bounded context visualization

### Goal

Expose the AI-system concept the project is trying to teach.

UI debug panel shows:

```text
conversation messages stored
messages included in current context
conversation summary
semantic/schema context size
recent observations
artifact references
current iteration
remaining tool budget
remaining token/cost budget
```

Do not expose secrets or hidden provider internals.

Add a deterministic context reducer.

### Done when

a multi-turn conversation demonstrates that:

```text
durable conversation != current LLM context
```

and older turns can be summarized/reduced.

Stop.

---

## Milestone 11 — Async job

### Goal

Demonstrate synchronous vs asynchronous execution.

Introduce one intentionally long analytical operation:

```text
create_full_report
```

Flow:

```text
App
 |
 | create job_id
 v
DynamoDB PENDING
 |
 v
Redis Stream: async-jobs
 |
 v
Worker
 |
 v
DuckDB
 |
 v
artifact
 |
 v
DynamoDB COMPLETED
 |
 v
run/job SSE event
```

The HTTP request should not wait for the whole report.

### Done when

UI shows:

```text
job_id
PENDING
RUNNING
COMPLETED
artifact available
```

Stop.

---

## Milestone 12 — Local integration hardening

### Goal

Treat local Docker Compose as a releasable product before touching AWS.

Run:

```text
frontend
ai-app
analytics-mcp
worker
redis
```

Run the Test/Release Reviewer.

Required scenarios:

```text
fresh startup
dataset download
health
MCP discovery
normal AI query
multi-turn conversation
app restart
bounded-loop failure
SSE
async job
```

No AWS changes yet.

### Done when

one documented local demo script passes end-to-end.

Stop.

---

## Milestone 13 — Terraform foundation only

### Goal

Provision AWS without deploying the full application yet.

Terraform creates the smallest useful foundation:

```text
ECR
S3 frontend bucket
S3 artifact bucket
DynamoDB
IAM roles
CloudWatch log groups
ECS cluster
```

Do not create CloudFront/ALB/ECS services until the foundation plan is reviewed.

Run:

```text
Terraform/AWS reviewer
```

### Done when

```text
terraform plan
terraform apply
terraform output
```

succeed and IAM roles match the documented ownership model.

Stop.

---

## Milestone 14 — Deploy backend to ECS

### Goal

Deploy the already-working containers without changing application semantics.

Add:

```text
ALB
ai-app ECS service
analytics-mcp ECS service
Service Connect
```

Initially validate:

```text
ALB /health
app -> MCP
app -> Bedrock
app -> DynamoDB
```

No frontend CloudFront yet.

### Done when

a curl request against the deployed backend performs one real AI analytics request.

Stop.

---

## Milestone 15 — S3 + CloudFront frontend

### Goal

Add the public polished entry point last.

Deploy React build to private S3.

CloudFront:

```text
/*      -> S3
/api/*  -> ALB
```

Verify SSE through CloudFront.

### Done when

the CloudFront URL provides the complete demo without requiring local services.

Stop.

---

## Milestone 16 — Final demo/review

### Goal

Polish evidence, not architecture.

Run review subagents in parallel because the work is primarily read/test/review:

```text
Frontend UX reviewer
LLM orchestration reviewer
MCP protocol reviewer
Persistence/state reviewer
Terraform/AWS reviewer
Test/release reviewer
```

Wait for all reviews.

Parent agent consolidates findings.

Fix only:

```text
demo-breaking bugs
architecture contradictions
security mistakes
unclear README/documentation
```

Do not add major new features.

Final README must include:

```text
deployed CloudFront link
architecture
one full request trace
state/storage model
MCP boundary
agent loop
SSE screenshots
budget/context screenshots
AWS IAM ownership
deliberate POC simplifications
how to destroy AWS resources
```

---

## Milestone status file

Maintain:

```text
docs/progress.md
```

Format:

```md
# Current milestone

Milestone 5 — One-turn LLM -> MCP

## Status
IN PROGRESS

## Acceptance criteria
- [x] tool definition supplied to model
- [x] tool proposal parsed
- [ ] MCP execution wired
- [ ] second model call
- [ ] integration test

## Decisions
- ...

## Known limitations
- ...

## Next milestone
Do not start until requested.
```

This file is the handoff contract between sessions.

---

## Parent-agent instruction template

For every Codex session, use a prompt shaped like:

```text
Read AGENTS.md, docs/implementation-plan.md, and docs/progress.md.

Work ONLY on Milestone <N>.

Before editing:
1. inspect current state;
2. state the smallest change needed;
3. identify any bounded research/review task worth delegating;
4. spawn subagents only for those independent tasks.

Implement the milestone.
Run its acceptance criteria.
Update docs/progress.md.

Do not start Milestone <N+1>.
Do not scaffold future components unless Milestone <N> explicitly requires them.

When done, report:
- files changed
- commands/tests run
- acceptance criteria result
- decisions made
- known limitations
- exact manual verification steps
```


## 21. Git, worktree, PR, and work-history contract

This repository is also intended to demonstrate disciplined incremental engineering.
Git history is a first-class artifact of the project.

### Core Git rule

Every meaningful implementation change must flow through a branch and pull request.

```text
milestone / bounded subtask
        |
        v
Git branch
        |
        v
dedicated Git worktree
        |
        v
dedicated Codex thread
        |
        v
small commits
        |
        v
PR
        |
        v
review / minimum merge gate
        |
        v
merge to main
        |
        v
post-merge smoke suite
        |
        v
work-history record finalized
```

Do not have multiple write-heavy Codex threads editing the same worktree.

Git worktrees are the preferred mechanism for parallel write work because they allow
multiple branches from the same repository to be checked out independently.

### Main-thread ownership

Keep one primary Codex thread as the project coordinator.

The primary thread owns:

```text
current milestone
docs/progress.md
cross-PR architectural decisions
integration order
merge order
post-merge verification
```

Parallel threads should receive a narrow branch/worktree and bounded objective.

Example:

```text
main coordinator thread
|
+-- worktree: ../wt-m2-dataset
|      branch: feat/m2-dataset-pin
|      thread: dataset scout + implementation
|
+-- worktree: ../wt-m3-ui
       branch: feat/m3-react-shell
       thread: UI shell
```

Only parallelize when changes are sufficiently independent.

Do NOT parallelize two branches that both need to redesign:

```text
shared orchestration core
shared DynamoDB schema
same Terraform resources
same API contract
```

unless the parent thread has first established a stable interface.

### Worktree lifecycle

Use commands shaped like:

```bash
git fetch origin
git switch main
git pull --ff-only

git worktree add ../wt-m1-mcp -b feat/m1-empty-mcp main
```

The Codex thread assigned to that work works only inside:

```text
../wt-m1-mcp
```

After the PR is merged:

```bash
git worktree remove ../wt-m1-mcp
git branch -d feat/m1-empty-mcp
git worktree prune
```

Do not reuse one worktree for unrelated branches.

### Branch naming

Use small descriptive names:

```text
feat/m0-health
feat/m1-empty-mcp
feat/m2-dataset-pin
feat/m2-duckdb-profile
feat/m3-react-shell
feat/m4-bedrock-client
feat/m5-single-tool-loop

infra/tf-foundation
infra/ecs-app-service

fix/sse-heartbeat
test/post-merge-smokes
docs/run-trace
```

A milestone may contain multiple PRs when that creates cleaner, reviewable history.

For example:

```text
Milestone 2
  PR A: pin dataset + download script
  PR B: add DuckDB initialization
  PR C: add MCP schema/profile resource
```

Prefer several understandable PRs to one large PR.

---

### Commit quality

Each commit should describe one meaningful change.

Prefer:

```text
feat(app): add health endpoint
feat(mcp): expose ping tool
test(mcp): verify app-to-mcp discovery
docs(dataset): pin yellow taxi parquet fixture
```

Avoid:

```text
changes
fix stuff
wip
final
more fixes
```

Temporary/WIP commits are acceptable inside a branch while actively developing, but before
merge either clean them up or make sure the resulting history is still understandable.

### Merge strategy

Default to a **merge commit** for project PRs so the history preserves:

```text
individual branch commits
PR boundary
merge point on main
```

The PR itself remains the unit of review/history.

For tiny documentation-only changes, squash merge is acceptable if it produces a clearer
history.

Do not rebase/rewrite already-merged history.

---

### PR creation

Use GitHub CLI where available.

Before opening:

```bash
git status
git diff --check
git log --oneline main..HEAD
```

Push:

```bash
git push -u origin <branch>
```

Create PR:

```bash
gh pr create \
  --base main \
  --head <branch> \
  --title "<small coherent change>" \
  --body-file <generated-pr-body>
```

The PR description must include:

```md
## Goal
Why this PR exists.

## Scope
What changed.

## Explicitly not included
What later milestone work was intentionally not done.

## Verification before merge
- command
- result

## Post-merge smoke
- command/check that should run after merge

## Architecture impact
None / exact boundary changed.

## Work history
Link/path to the corresponding work-history entry.
```

### Minimum merge gate

The project intentionally allows some expensive testing to happen **after merge** to preserve
small iteration speed, but every PR must pass a minimal local gate before merge.

Minimum gate:

```text
git diff --check
relevant formatter/linter if configured
code imports/compiles
smallest relevant unit test or health check
```

Examples:

```text
Python change
  -> python import/compile + relevant pytest

React change
  -> npm build or typecheck

Terraform change
  -> terraform fmt -check + terraform validate

Docker change
  -> image builds

docs-only change
  -> diff check / links or formatting if available
```

It is acceptable to merge before running:

```text
full Docker Compose regression
real Bedrock integration
real AWS deploy
CloudFront end-to-end test
long DuckDB workload
complete browser smoke suite
```

when all of these are true:

1. the PR is small and reversible;
2. its minimum merge gate passed;
3. the PR explicitly lists the deferred test;
4. the post-merge smoke suite runs immediately after merge;
5. failures create a follow-up fix PR rather than untracked edits on main.

Do not merge knowingly broken code merely to create history.

---

### Merge

Preferred:

```bash
gh pr merge <PR> --merge --delete-branch
```

If repository protection/auto-merge is enabled, using GitHub auto-merge is also acceptable.

After merge, the coordinator returns to the main worktree:

```bash
git switch main
git pull --ff-only
```

Then execute the cumulative smoke suite.

---

## 22. Cumulative smoke-test ladder

Maintain a deliberately small set of smoke tests that grows after each merged PR.

The purpose is not comprehensive QA.

The purpose is:

```text
main still starts
previous milestone behavior still works
new behavior is reachable
critical architectural boundaries still hold
```

Maintain executable smoke commands in:

```text
scripts/smoke/
```

Suggested structure:

```text
scripts/smoke/
├── 00_health.sh
├── 01_mcp_ping.sh
├── 02_dataset_profile.sh
├── 03_frontend_build.sh
├── 04_llm_basic.sh
├── 05_llm_mcp_roundtrip.sh
├── 06_analytics_query.sh
├── 07_persistence_restart.sh
├── 08_agent_budget.sh
├── 09_sse_events.sh
├── 10_context_reduction.sh
├── 11_async_job.sh
├── 12_local_e2e.sh
├── 13_tf_validate.sh
├── 14_aws_backend.sh
└── 15_cloudfront_e2e.sh
```

Also provide:

```bash
make smoke
```

which runs every smoke currently applicable to the repository state.

Early in the project, `make smoke` may execute only:

```text
00_health.sh
```

After MCP lands:

```text
00_health.sh
01_mcp_ping.sh
```

After dataset:

```text
00_health.sh
01_mcp_ping.sh
02_dataset_profile.sh
```

Continue growing this list.

### Smoke-test design rules

Each smoke should be:

```text
small
readable
fast when possible
deterministic
safe to rerun
explicit about prerequisites
```

A smoke test should fail loudly with a useful message.

Do not turn the smoke suite into a slow full integration-test framework.

### Post-merge protocol

After every merge:

```text
1. update local main;
2. run `make smoke`;
3. record outcome in work history;
4. if smoke passes, mark PR history COMPLETE;
5. if smoke fails, open a new fix branch/worktree/PR immediately.
```

Never fix main directly.

---

## 23. Work-history ledger

Maintain a permanent engineering journal under:

```text
docs/work-history/
```

Every PR gets one entry.

Suggested filename:

```text
docs/work-history/
  0001-m0-health.md
  0002-m1-empty-mcp.md
  0003-m2-dataset-pin.md
  ...
```

Use monotonically increasing project sequence numbers independent of GitHub PR numbering.
This lets the file be created before the PR number is known.

Template:

```md
# Work 0003 — Pin NYC Taxi dataset

## Milestone
Milestone 2 — Dataset spike

## Branch
feat/m2-dataset-pin

## Worktree
../wt-m2-dataset

## Codex thread / agent
Dataset task

## Goal
Pin one reproducible official NYC TLC Parquet dataset.

## Starting state
What existed before this work.

## Changes
- ...

## Key decisions
- ...

## Alternatives considered
- ...

## Commits
- <sha> <message>

## Pull request
PR #<number>
<GitHub PR URL if appropriate>

## Pre-merge verification
- command
- result

## Deferred verification
- ...

## Merge
- merge commit SHA
- merged timestamp

## Post-merge smoke
- `make smoke`
- PASS / FAIL
- relevant output summary

## Follow-up
- none
or
- Work 0004 / PR #...

## Lessons
Short engineering notes only.
```

The work-history file should be created as part of the branch and updated with its PR number
before merge when practical.

If the PR number cannot be added before merge, add it in the next tiny documentation PR.
Do not edit `main` directly.

### Global work-history index

Maintain:

```text
docs/work-history/README.md
```

with a compact ledger:

```text
| Work | Milestone | PR | Description | Result |
|------|-----------|----|-------------|--------|
| 0001 | M0 | #1 | FastAPI health | PASS |
| 0002 | M1 | #2 | Empty MCP service | PASS |
| 0003 | M2 | #3 | Pin taxi dataset | PASS |
```

This index should make the project evolution understandable without reading every PR.

---

## 24. Decision log versus work history

Use separate concepts:

```text
docs/work-history/
    What happened in each PR.

docs/decisions/
    Architectural decisions that should survive individual PRs.
```

When a PR makes a material architectural decision, add a small ADR.

Example:

```text
docs/decisions/
├── 0001-app-owns-llm-loop.md
├── 0002-mcp-is-separate-service.md
├── 0003-dynamodb-durable-state.md
├── 0004-redis-is-transient.md
└── 0005-cloudfront-single-origin.md
```

Do not create ADRs for trivial implementation details.

---

## 25. Local AWS developer access

For this single-user POC, local Terraform and AWS CLI commands may use the already-authenticated
default AWS developer profile. Before any AWS-dependent Terraform command, verify that it targets
the intended account:

```bash
aws sts get-caller-identity
```

The expected account is:

```text
107207236011
```

AWS IAM Identity Center / AWS CLI SSO is optional future hardening, not a prerequisite or
acceptance blocker for this POC. Do not commit a profile name or export a developer-specific
`AWS_PROFILE` in project tooling.

### Credential safety

Do not create long-lived IAM user access keys for Codex or the developer machine. Never commit,
copy into the repository, or write to `.env`:

```text
~/.aws/config
~/.aws/credentials
~/.aws/sso/cache
access keys
session tokens
temporary AWS credentials
```

Codex may use an already-authenticated local profile exposed to its environment, but must not
print cached tokens, create access keys, or place credentials in Terraform variables, GitHub
Actions, Docker images, task definitions, or containers.

### Deployed workload credentials

This local operator choice does not apply to deployed workloads. ECS containers must obtain
temporary AWS credentials from their separate ECS task roles. Do not provide static credentials
to ECS task definitions or application runtime configuration.

---

## 26. Parallel PR protocol for Codex

When the coordinator sees two truly independent tasks, it may open parallel Codex threads.

Example:

```text
M2 dataset work
|
+-- Thread A / worktree A
|      pin public dataset + download verifier
|
+-- Thread B / worktree B
       draft deterministic demo-question fixture
```

Both branches may open PRs.

The parent coordinator decides merge order.

Before the second PR merges:

```text
git fetch origin
```

and rebase/merge updated `main` into that branch if required so integration conflicts are
resolved deliberately.

### Parallelization checklist

Parallel work is allowed only if:

```text
[ ] separate worktree
[ ] separate branch
[ ] separate Codex thread
[ ] bounded goal
[ ] mostly independent files/interfaces
[ ] PR can be reviewed alone
[ ] parent thread owns merge ordering
```

If these conditions are not true, work sequentially.

### Reviewer subagents and Git

Read-only reviewer subagents do not require a new branch/worktree.

Write-capable subagents should receive their own branch/worktree unless the parent explicitly
delegates ownership of the parent's current branch and no other writer is active there.

---

## 27. Project-history acceptance criteria

The project is not considered complete unless the Git history itself demonstrates the phased
development.

Final repository should have:

```text
multiple small PRs
meaningful commit messages
merge points for major PRs
work-history entry for every PR
cumulative smoke suite
architectural decision records
no direct feature edits to main
no committed credentials
clear milestone progression
```

The README should include a section:

```text
## How this project evolved
```

linking to:

```text
docs/work-history/README.md
docs/decisions/
docs/implementation-plan.md
```

This is part of the demo: the repository should show not only the final architecture, but how
the architecture was built and validated incrementally.


## 28. Repository structure

```text
/
├── README.md
├── AGENTS.md
├── .editorconfig
├── pyproject.toml
├── Makefile
├── docker-compose.yml
├── frontend/
├── services/
│   ├── app/
│   ├── mcp/
│   └── worker/
├── infra/
│   └── terraform/
├── scripts/
│   ├── download_dataset.py
│   ├── seed_demo.py
│   ├── build_and_push_images.sh
│   ├── deploy_backend.sh
│   └── deploy_frontend.sh
├── scripts/
│   └── smoke/
│       ├── 00_health.sh
│       └── ...
└── docs/
    ├── implementation-plan.md
    ├── progress.md
    ├── work-history/
    │   ├── README.md
    │   └── 0001-....md
    ├── decisions/
    │   └── 0001-....md
    ├── architecture.md
    ├── sequence.md
    └── learnings.md
```

Local docker-compose should run:

```text
frontend
app
mcp
worker
redis
```

## 29. README

Lead with:

1. deployed CloudFront demo link
2. architecture diagram showing CloudFront -> S3 and CloudFront -> ALB -> ECS
3. what the project demonstrates
4. one example request trace
5. local-run instructions
6. screenshots/GIF of the run-step UI
7. design decisions
8. deliberate POC simplifications
9. lessons learned

Do not frame the README as correcting an interview.

Frame it as:

> A small production-shaped AI analytics application exploring durable agent orchestration, MCP tool boundaries, streaming UX, bounded context, and cost-aware execution.

## 30. Acceptance criteria

The POC is done when:

- Terraform can provision the complete AWS demo environment from a clean account/configuration.
- No long-lived AWS access keys are stored in the repo, task definitions, or application environment.
- The `ai-app` container accesses Bedrock/DynamoDB/S3 through its ECS task IAM role.
- The React UI is publicly accessible through the CloudFront distribution URL.
- A user can start a conversation and ask a taxi-data question.
- Conversation/message/run state survives backend restart.
- Browser receives SSE progress events.
- UI visibly shows orchestration steps.
- App discovers and invokes a separate FastMCP service.
- FastMCP executes DuckDB against real Parquet data.
- LLM proposes a tool call and receives the bounded result.
- App can execute more than one loop iteration.
- Loop terminates on final answer or hard budget.
- Redis Streams carry run events and one async job.
- A long report returns a `job_id` and completes asynchronously.
- Run cost/token counters appear in the UI.
- Large tool results are bounded and never blindly sent to the LLM.
- README explains component ownership, protocols, state, and key trade-offs.
- Every meaningful implementation change was merged through a PR.
- Every PR has a corresponding `docs/work-history/` entry.
- `make smoke` represents the cumulative post-merge smoke-test ladder.
- A single local Terraform operator verifies default-profile access to account `107207236011`;
  SSO is optional future hardening, and no long-lived access keys are used.
- End-user authentication is intentionally absent and documented as out of scope.
- Hosted Bedrock calls stop when the application-side global demo allowance is exhausted.
- AWS Budget alerts are configured as a secondary billing safety net.
- `main` has no direct feature pushes after the bootstrap commit.
- Python code passes Black, Ruff, and relevant pytest checks.

---
name: local-docker-test
description: Instructions and exact commands to start, verify, and stop the local Docker AWS stack (Bedrock, Transcribe, Polly, DynamoDB, Redis) inside any project-local worktree. Use whenever asked to "start docker to test", "manual testing", "test from worktree", "run local aws", or before asking the user to manually verify a feature.
---

# Local Docker AWS Stack for Worktree Manual Testing

Use this workflow whenever the user asks for manual testing (e.g., "start docker to test", "i can help with manual testing", "run local aws"), or when you need to verify changes live in an isolated worktree before opening/merging a PR.

## 1. Pre-flight: Check for Port Conflicts

Check if port 3000 or another Docker Compose project is already running:
```bash
docker ps
```
If an older or different Compose project is occupying port 3000:
```bash
# Stop the conflicting project by its project name, e.g.:
docker compose -p <conflicting-project-name> down
```

## 2. Start the Stack from within the Worktree

From inside the worktree directory (e.g., `.worktrees/<topic>`):

**Quick start (restart from scratch):**
```bash
make local-aws-refresh
```

**Or start (keep running stack):**
```bash
make local-aws-compose
```

**With custom settings:**
```bash
DYNAMODB_TABLE_NAME=custom-table AWS_PROFILE=profile-name WEB_PORT=3001 make local-aws-compose
```

**Defaults (if not set):**
- `DYNAMODB_TABLE_NAME=ai-analytics-poc-demo-application-state`
- `AWS_PROFILE=default`
- `WEB_PORT=3000`

> **Note**: Docker Compose automatically uses the worktree directory name as the Compose project name, ensuring network and container isolation.

## 3. Verify Health

Wait a few seconds for services to become healthy, then curl the health endpoint:
```bash
curl -s http://localhost:3000/api/status
```
Expected output:
```json
{"app":{"status":"ok","service":"ai-app"},"mcp":{"status":"ok","tools":3,"resources":1}}
```

## 4. Inspect Container Logs (if troubleshooting)

```bash
# View backend application logs:
docker compose logs --tail 50 -f app

# View all services:
docker compose logs --tail 30
```

## 5. Teardown / Clean Up

When manual testing is complete:
```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml down
```

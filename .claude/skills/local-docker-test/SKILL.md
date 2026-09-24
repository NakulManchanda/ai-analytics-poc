---
name: local-docker-test
description: Use when asked to start Docker for testing, run the local AWS stack, test from a worktree, or hand off a live local feature for manual verification.
---

# Local Docker testing

Use the repository's root `Makefile` as the operator interface. Do not create a second Makefile or reconstruct Compose commands already represented by a target.

## Prepare

Inspect `make help` and the relevant target before running it. Check `docker ps` for port conflicts, but never stop another task's Compose project. Select task-specific ports or a task-owned project instead.

## Start from the active worktree

Give the user a short, copyable sequence beginning with the real worktree path:

```bash
cd <active-worktree>

# Fake-provider stack when real AWS is unnecessary:
WEB_PORT=<task-owned-port> \
JAEGER_UI_PORT=<task-owned-port> \
make observability-up

# Or the opt-in real-AWS overlay when the test requires it:
DYNAMODB_TABLE_NAME=<shared-state-table> \
AWS_PROFILE=<local-profile> \
WEB_PORT=<task-owned-port> \
make local-aws-compose
```

Use only the path required by the acceptance test. Real AWS is opt-in and may make paid calls; never print or commit credentials.

## Verify and hand off

Check the applicable health endpoint before asking the user to test:

```bash
curl -s http://localhost:<web-port>/api/status
```

Then give only the useful repeatable targets and URLs. Prefer existing targets such as `observability-smoke`, `compose-smoke`, `dashboard`, and service-specific test targets. Add a small root target only when a non-trivial command has a demonstrated recurring use; keep one-off inspection commands direct.

## Stop

From the same task-owned worktree, use its existing down target when available. Otherwise run the matching Compose down command with the exact files, ports, and project owned by this task. Never stop, reuse, or clean up another task's stack.

# Current milestone

Milestone 0 — FastAPI health endpoint

## Status

IMPLEMENTED — awaiting review and merge

## Acceptance criteria

- [x] `make dev` serves the FastAPI application on port 8080
- [x] `GET /health` returns `200` and `{"status":"ok","service":"ai-app"}`
- [x] FastAPI `TestClient` contract test passes
- [x] Smoke script verifies the live endpoint
- [x] Container builds and runs as a non-root user; `/health` returns the exact contract
- [ ] Pull request reviewed and merged

## Decisions

- The application uses an app factory and a dedicated health router.
- Service dependencies and their lockfile live under `services/app`.
- Port 8080 is the single local and container application port.
- The container pins uv 0.9.26 to match the local lock workflow and runs as UID 10001.
- The requirements document is now a tracked canonical source; its former ignored-local-input
  bootstrap treatment is retired.

## Known limitations

- This milestone intentionally contains no MCP, React, Redis, AWS, or LLM integration.

## Next milestone

Milestone 1 — empty FastMCP service. Do not start until explicitly requested after Milestone 0
is reviewed and merged.

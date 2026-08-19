# Current milestone

Milestone 3 — minimal React shell showing the application workflow

## Status

IMPLEMENTED — awaiting review and merge

## Acceptance criteria

- [x] React page visibly shows the title, FastAPI health, MCP discovery health, disabled prompt,
  and placeholder timeline
- [x] FastAPI owns MCP discovery and returns a bounded status summary at `GET /api/status`
- [x] Docker Compose provides browser → FastAPI → FastMCP through a same-origin `/api/` proxy
- [x] Focused React rendering test and production build pass
- [x] Local Compose and in-app browser smoke show both services healthy
- [ ] Pull request reviewed and merged

## Decisions

- The React container serves static assets through Nginx and proxies only `/api/` to FastAPI, so
  the browser has no direct access to the private MCP service.
- FastAPI performs the MCP protocol discovery and returns only service status plus tool/resource
  counts. This keeps LLM ownership and future run orchestration with the application service.
- The prompt is disabled and the timeline is static: no LLM, chat persistence, SSE, tools UI,
  Redis, or future orchestration interfaces are implemented in this milestone.
- Compose waits for the MCP TCP listener and FastAPI `/health` before starting the browser; the UI
  also retries its bounded status request to recover from a transient startup failure.

## Known limitations

- MCP discovery reports the M2 fixed profile surface (`1 tool`, `1 resource`), but does not expose
  it as browser tool UI. Actual prompt execution, durable conversations, streaming events, and
  context visualization remain later milestones.

## Next milestone

Next milestone: Milestone 4 — first real Bedrock call owned by the application, with no tools.

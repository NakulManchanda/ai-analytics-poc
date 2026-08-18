# Work 0009 — Milestone 3 React UI

## Goal

Introduce the smallest browser-visible application workflow: a title, FastAPI health, FastMCP
discovery health, inert prompt placeholder, and static timeline placeholder.

## Starting state

Main at `053b43e` had the M0 FastAPI `/health` endpoint, the independently runnable empty M1
FastMCP service, and the merged M2 dataset spike. It had no React app, Compose topology, or
application-owned MCP discovery endpoint.

## Decisions

- Serve the Vite-built React assets through Nginx and proxy only same-origin `/api/` requests to
  FastAPI. The browser cannot connect to FastMCP directly.
- Add `GET /api/status` to FastAPI. It owns the MCP protocol connection, returns only status and
  tool/resource counts, and preserves a healthy app response when MCP is unavailable.
- Use a disabled prompt and a static three-step timeline so the page communicates the intended
  workflow without prebuilding chat, LLM execution, persistence, streaming, or tools UI.
- Pin frontend package versions and exclude Node build artifacts from Git and Docker contexts.

## TDD evidence

The FastAPI status tests were added before `app.routers.status` existed and failed with an import
error. The React rendering test was added before `App.tsx` existed and failed with a missing-module
error. The minimal endpoint and component then made both focused suites pass.

## Verification

- `uv run --project services/app pytest services/app/tests/test_health.py -q` — 3 passed.
- `npm test` in `web/` — 1 passed.
- `npm run build` in `web/` — production Vite build passed.
- `docker compose up --build -d`, then `curl http://localhost:3000/api/status` — returned app
  status `ok` and MCP status `ok` with zero tools/resources.
- In-app browser smoke at `http://localhost:3000/` — visible title, backend-ready label,
  MCP-discovered label, and disabled prompt verified.

## Pull request and merge

Draft PR: https://github.com/NakulManchanda/ai-analytics-poc/pull/19

Merge status: awaiting review and explicit authorization. Do not merge without authorization.

## Lessons

The UI can demonstrate the service boundary before any AI functionality exists: relative browser
requests terminate at FastAPI, while MCP discovery remains a server-side concern.

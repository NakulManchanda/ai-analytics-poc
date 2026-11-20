# 0136 — Trusted map renderer and zone-map artifact v1 (#234, epic #231)

## Goal
Deliver a declarative, validated `map_artifact` v1 contract, an app-owned live zone-map endpoint backed by a governed MCP tool, and an interactive plain-SVG NYC taxi-zone renderer with accessible table fallback and chat integration.

## Starting point
`web/` used plain React 19 + Vite with no map library and no zone geometry in the repository; `taxi_zone_lookup.csv` had 265 rows, and existing planner tools (`aggregate_taxi_data`) returned zone names capped at 20 rows rather than all 263 zone ids.

## Decisions
- **Versioned geometry + plain SVG:** Converted official TLC `taxi_zones.zip` (100 ft tolerance, WGS84, 263 features, ids `1..263`) via `scripts/build_taxi_zone_geometry.py` using ephemeral `uv --with` dependencies; committed `web/src/map/taxi-zones.v1.json` (loaded as a lazy Vite chunk) and `services/app/app/artifacts/taxi_zones_v1.json` (server allowlist). No external map library or tiles.
- **Dual validation & golden fixtures:** Implemented `MapArtifact` v1 (`extra="forbid"`, 64 KB cap, closed enums, nullable `run_id`/`tool_call_id` for non-run map queries) in `services/app/app/artifacts/map_artifact.py` and mirrored it in `web/src/map/artifact.ts`, backed by shared valid and invalid fixtures.
- **App-owned live path:** Added read-only MCP tool `map_taxi_zones` (`services/dataset_spike` + `services/mcp`) and `POST /api/map/zones` (`services/app/app/routers/maps.py`) with a 64-entry LRU cache and non-blocking startup pre-warming (`warm_curated_zones` in `services/app/app/main.py` lifespan) for the six curated pickup measures. `map_taxi_zones` is excluded from planner-offered tool lists so the planner prompt and prefix contract remain unchanged.
- **Interactive web panel & chat link:** Added `MapPanel`, `api.ts` (client cache + `AbortController` + 150 ms debounce), memoized `PathLayer` + delegated `<svg>` hover/click in `ZoneMap.tsx`, borough view boxes, "Ask about this zone" prompt fill, and "Show on map" when `tool.requested` SSE events carry `aggregate_taxi_data` with `pickup_zone` or `dropoff_zone`.

## Verification
- `services/dataset_spike`: 65 passed; `services/mcp`: 59 passed; `services/app/tests`: 698 passed, 1 skipped (live LiteLLM); root `tests`: 98 passed, 1 skipped; `tooling/evals`: 205 passed, 2 skipped.
- `black --check` and `ruff check` clean across `services/app`, `tests`, `services/gateway`, `services/mcp`, and `services/dataset_spike`.
- `make web-test`: 13 test files, 116 tests passed; `tsc --noEmit` and `vite build` succeeded (`taxi-zones.v1` chunk 211.26 kB raw / 55.67 kB gzip).
- Isolated Docker Compose verification (`app`, `mcp`, `redis`): `POST /api/map/zones` cache miss 938–2,590 ms (median ~1,370 ms, dominated by spawned DuckDB process in MCP), cache hit 1.0–4.7 ms (`x-map-cache: hit`), 15–17 KB raw (3.7–4.3 KB gzip), 422 rejections verified.

## Limitations / lessons
- Cold `POST /api/map/zones` misses the 500 ms target because `_run_governed_analytics_tool` spawns a fresh DuckDB subprocess per query; client/server caching mitigates repeat views, and a persistent MCP query worker is a separate decision.
- Per-polygon 100 ft simplification leaves minor hairline gaps between neighboring zones.
- Browser manual check of `?demo=map` and live `MapPanel` interactions is handed off via the local Docker run guide.

## PR / merge state
Draft PR #250 on branch `feat/234-spatial-map-renderer` (worktree `.worktrees/234-spatial-map-renderer`).

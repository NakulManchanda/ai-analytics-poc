# Map Artifact v1 and Zone Map Renderer

Issue `#234` adds a declarative, validated `map_artifact` v1 contract, an app-owned live endpoint (`POST /api/map/zones`), a governed MCP tool (`map_taxi_zones`), and an interactive plain-SVG zone map renderer (`web/src/map/`).

The model never writes map code, SQL, coordinates, or geometry. Artifacts are built deterministically by the FastAPI application from governed tool results, validated on both the server and the client, and rendered without external tiles or network calls at render time.

## Architecture and data flow

```text
Live map panel / "Show on map":
  web MapPanel -> POST /api/map/zones (FastAPI, no LLM)
               -> FastMCP map_taxi_zones -> DuckDB/Parquet
               -> build_zone_choropleth_from_ids + validate_map_artifact
               -> web parseMapArtifact -> ZoneMap (SVG + table fallback)
```

- **App owns orchestration and validation:** `services/app/app/routers/maps.py` validates request inputs against closed measure and filter allowlists, calls MCP, builds and validates the `map_artifact` v1 payload, and caches serialized bytes in an in-process 64-entry LRU. On startup (`services/app/app/main.py` lifespan), a background daemon thread pre-warms the six curated pickup measures (`trip_count`, `average_fare`, `average_tip`, `tip_rate`, `average_distance`, `average_duration_minutes`) into that LRU cache (fail-open if MCP is unavailable).
- **Planner isolation:** `map_taxi_zones` is an app-only MCP tool. It is excluded from planner-offered tool lists on both the manual ReAct path and Deep Agents (`_NON_PLANNER_MCP_TOOLS`), keeping the planner prompt and prefix contract unchanged.
- **Chat link:** When a completed run's `tool.requested` SSE event carries `aggregate_taxi_data` with `pickup_zone` or `dropoff_zone` in `dimensions`, the UI shows a **Show on map** button under that answer. Clicking it opens `MapPanel` prefilled with the zone role, first measure, and supported filters; the client sends those parameters to `POST /api/map/zones` for server validation.

## Schema v1 (`map_artifact`)

Canonical Pydantic v2 model (`extra="forbid"`, frozen) in `services/app/app/artifacts/map_artifact.py`, mirrored by `web/src/map/artifact.ts`:

- `schema`: `"map_artifact"`, `version`: `1`
- `artifact_id`: `"map_<hex>"` (deterministic hash of evidence fields and parameters)
- `geography`: `{"id": "nyc_tlc_taxi_zones", "version": "v1"}`
- `evidence`: `run_id` (`string | null`), `tool_call_id` (`string | null`), `query_id` (`string`), `tool_name` (`"aggregate_taxi_data" | "map_taxi_zones"`), `dataset` (`"nyc-yellow-taxi-2024-01"`)
- `title`: max 120 chars, no control characters
- `layers`:
  - Exactly one `choropleth` layer (`measure`, `unit` ≤ 24 chars, `scale` with `kind: "sequential"`, `palette: "blues" | "oranges"`, finite `min <= max`, and up to 263 `regions` of `{label, location_ids[], value}`).
  - At most one `highlight` layer (`label`, `location_ids[]`).
  - `hotspot` is reserved and rejected in v1 (`unsupported_layer`).
- `unmapped`: rows whose zone id or name has no geometry (`reason: "no_geometry" | "unknown_zone"`), rendered in the table fallback only.
- `filters`: up to 16 scalar key/value pairs echoed from the request.
- `view`: `{"fit": "all"}` or `{"fit": {"location_ids": [...]}}` (allowlisted zone ids only; no raw coordinates or zoom numbers).
- `provenance`: `generated_by: "app"`, `source`, `interpretation: "spatial_pattern_not_causal"`.

### Limits and threat model

- **Allowlisted geometry only:** Every `location_id` must exist in `services/app/app/artifacts/taxi_zones_v1.json` (ids `1..263`). Duplicate zone ids across choropleth regions are rejected.
- **Size and string bounds:** Serialized artifact size is capped at 64 KB (`65,536` bytes). Strings forbid control characters; `title` ≤ 120, labels ≤ 80, `unit` ≤ 24, `filters` ≤ 16 keys.
- **No markup or URLs:** The renderer uses React text nodes only (no `dangerouslySetInnerHTML`), closed palette enums, and no external tile servers or network requests at render time.
- **Closed error codes:** `validate_map_artifact` raises `MapArtifactError` with closed codes (`invalid_schema`, `unknown_zone`, `unsupported_layer`, `invalid_value`, `too_large`, `invalid_result`). `POST /api/map/zones` maps MCP/request rejections to HTTP `422` (`invalid_request`) and MCP transport/envelope failures to HTTP `502` without echoing raw exception text.

## Geometry asset and regeneration

- **Committed assets:**
  - `web/src/map/taxi-zones.v1.json` — WGS84 GeoJSON `FeatureCollection` (263 features, ids `1..263`, bbox `[-74.25559, 40.49613, -73.70002, 40.91553]`, 100 ft simplification tolerance, 5-decimal coordinate rounding). Loaded asynchronously as a separate Vite chunk.
  - `services/app/app/artifacts/taxi_zones_v1.json` — server-side zone index and metadata.
- **Pinned source:** `[dataset.zone_geometry]` in `config/datasets/nyc_yellow_taxi_2024_01.toml` pins the official TLC `taxi_zones.zip` SHA-256 (`f6d711917bb4340f8f644d5366c51665489eb2d426dd1a4a55677721ae5adf17`, 263 expected features).
- **Regenerate deterministically:**

```bash
make zone-geometry
# or from a local archive:
make zone-geometry ZONES_ZIP=/path/to/taxi_zones.zip
```

The script (`scripts/build_taxi_zone_geometry.py`) runs via `uv run --no-project --with pyshp --with pyproj --with shapely` so no runtime geometry dependencies are added to `services/app` or `web`. Per-polygon simplification at 100 ft tolerance can leave minor hairline gaps between neighboring polygons at city scale.

## Endpoint and UI demo

- **Fixture demo:** Open `/?demo=map` in the web UI to render the golden fixture (`web/src/map/fixtures/pickup-count-choropleth.v1.json`) under a "Fixture demo, not live data" banner without starting a backend run.
- **Live endpoint:** `POST /api/map/zones` accepts `{"zone_role": "pickup" | "dropoff", "measure": "<measure>", "filters": {...}}` and returns `Cache-Control: private, max-age=300`, `x-map-cache: hit | miss`, and `Server-Timing`.

## Measured local Docker performance

Measured in an isolated local Compose stack (`app`, `mcp`, `redis`) against `http://127.0.0.1:8080/api/map/zones` across 10 distinct requests:

- **Cache miss:** 938–2,590 ms (median ~1,370 ms); dominated by `Server-Timing: mcp` (934–2,503 ms) because governed DuckDB queries spawn a fresh subprocess per call (`services/dataset_spike/dataset_spike/analytics.py`), while artifact `build` takes 3–10 ms (one 116 ms outlier).
- **Cache hit:** 1.0–4.7 ms server time (median ~1.5 ms) with `x-map-cache: hit`.
- **Payload and bundle sizes:** Artifact responses 15–17 KB raw (3.7–4.3 KB gzip) covering 229–259 regions; production Vite geometry chunk `taxi-zones.v1-*.js` is 211.26 kB raw (55.67 kB gzip).

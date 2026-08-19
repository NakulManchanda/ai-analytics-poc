# Current milestone

Milestone 2 — reproducible NYC TLC dataset spike with DuckDB

## Status

IMPLEMENTED — awaiting review and merge

## Acceptance criteria

- [x] Official January 2024 Yellow Taxi Parquet and zone lookup are pinned with SHA-256 metadata
- [x] The local ignored cache is verified before reuse and invalid files are replaced atomically
- [x] DuckDB opens the Parquet with `threads=1` and `memory_limit=512MB`
- [x] Fixed profile verifies schema, 2,964,624 trips, 265 zones, and a bounded day/zone join
- [x] FastMCP startup reuses/downloads the pinned data and exposes `dataset://nyc-taxi/schema` and
  `get_dataset_profile()` without accepting SQL
- [x] Fixture tests and manual/cached live smoke record timing and process high-water RSS
- [x] Ordinary CI runs fixture tests only; it does not download the public Parquet artifact
- [x] M0 and M1 remain included in the cumulative smoke
- [ ] Pull request reviewed and merged

## Decisions

- The MCP service depends directly on the standalone `services/dataset_spike` package and exposes
  its fixed profile through a schema resource and no-argument tool; it does not introduce app,
  LLM, Redis, AWS, or React coupling.
- Only the two static profile inputs are accepted; the DuckDB queries are fixed in code and `top_n`
  is bounded to 1–100.
- Cache files are local, ignored, and checksum-verified; the Parquet size is also pinned.

## Known limitations

- The fixed profile contains small joined aggregation rows for M2 inspection only; the future query
  tool will need its own allowlisted inputs, result-byte limit, query ID, and truncation contract.
  The manual live smoke requires network access on a cold local cache.

## Next milestone

Next milestone: Milestone 3 — minimal React shell showing the application workflow.

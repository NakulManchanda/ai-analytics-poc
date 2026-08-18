# Current milestone

Milestone 2 — reproducible NYC TLC dataset spike with DuckDB

## Status

IMPLEMENTED — awaiting review and merge

## Acceptance criteria

- [x] Official January 2024 Yellow Taxi Parquet and zone lookup are pinned with SHA-256 metadata
- [x] The local ignored cache is verified before reuse and invalid files are replaced atomically
- [x] DuckDB opens the Parquet with `threads=1` and `memory_limit=512MB`
- [x] Fixed profile verifies schema, 2,964,624 trips, 265 zones, and a bounded day/zone join
- [x] Fixture tests and manual/cached live smoke record timing and process high-water RSS
- [x] Ordinary CI runs fixture tests only; it does not download the public Parquet artifact
- [x] M0 and M1 remain included in the cumulative smoke
- [ ] Pull request reviewed and merged

## Decisions

- The spike is a standalone Python project under `services/dataset_spike`, so M2 does not alter
  MCP exposure or introduce application, LLM, Redis, AWS, or React coupling.
- Only the two static profile inputs are accepted; the DuckDB queries are fixed in code and `top_n`
  is bounded to 1–100.
- Cache files are local, ignored, and checksum-verified; the Parquet size is also pinned.

## Known limitations

- The M2 data layer is not yet exposed through MCP. MCP schema/profile resources and tools remain
  later work. The manual smoke requires network access on a cold local cache.

## Next milestone

Next milestone: Milestone 3 — minimal React shell showing the application workflow.

# Work 0007 — Milestone 2 dataset spike

## Goal

Prove a reproducible, local NYC TLC January 2024 Yellow Taxi data layer with DuckDB before any
MCP, AI, or application integration.

## Starting state

Main at `956b538` contained a FastAPI health service and an intentionally empty FastMCP service.
No dataset, DuckDB dependency, or cache downloader existed.

## Decisions

- Pin the official Parquet URL, 49,961,641-byte size, SHA-256, and expected 2,964,624 rows in
  tracked TOML. Pin the official zone CSV URL, SHA-256, and expected 265 rows alongside it.
- Put the implementation in `services/dataset_spike`, separate from both runtime services.
- Reuse only size/checksum-verified cache files in ignored `data/`, otherwise download to a
  temporary suffix and atomically replace the cache entry after verification.
- Constrain DuckDB to one thread and a 512 MB memory limit. Run fixed schema, count, lookup-count,
  and `top_n <= 100` day/zone aggregation queries only; callers cannot submit SQL.

## TDD evidence

The initial four fixture tests were written before the package and failed with
`ModuleNotFoundError: dataset_spike`. The minimal config, downloader, and fixed profile made them
pass. A fifth test for the executable orchestration was added first and failed before its module
and row-count validation were implemented.

## Verification

- Fixture suite: `uv run --project services/dataset_spike pytest services/dataset_spike/tests`
  — 5 passed.
- Live/manual smoke: `make dataset-smoke` — 2,964,624 Parquet rows, 265 zone rows, expected
  schema, bounded joined aggregation, 216 ms profile time, and 73,203,712-byte high-water RSS on
  the development machine.
- Ordinary CI installs and tests the dataset project without invoking the live smoke or downloading
  the public Parquet artifact.

## Pull request and merge

Draft PR: https://github.com/NakulManchanda/ai-analytics-poc/pull/12

Merge status: awaiting review and exact-head CI. Do not merge without explicit authorization.

## Lessons

Keeping deterministic data work outside MCP makes it straightforward to verify provenance,
resource bounds, and read-only query behavior before introducing an external tool contract.

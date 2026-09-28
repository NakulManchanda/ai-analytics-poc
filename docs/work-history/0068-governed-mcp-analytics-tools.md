# Work history 0068 — Governed MCP analytics tools (#115 slice A)

## Goal

Give the ReAct agent workload (#115) a governed analytical MCP surface broad enough that ReAct is
not evaluated against an artificially tiny tool set, without letting any caller- or model-supplied
SQL, column name, or file path reach DuckDB. This is Slice A only: the governed MCP analytics
surface. Slices B (honest model calls), C (deterministic/ReAct loop) and D (eval corpus) are
separate, independent PRs.

## Starting point

- `services/dataset_spike/dataset_spike/analytics.py` had `_run_governed_query`: a killable
  spawn subprocess with a hard deadline, 512 MB memory limit, 20-row cap and an 8 KiB response
  envelope, over the `trips` and `taxi_zones` views.
- `services/mcp/mcp_server/server.py` exposed three fixed tools: `get_dataset_profile`,
  `query_taxi_data` (3 fixed analyses) and `average_trip_metrics(region_name?)`.
- No typed dimension/measure/filter model existed; adding an analytical question meant hand-writing
  a new fixed SQL string.
- Dataset facts (experiment e1, pinned Jan 2024 TLC yellow taxi trip data): 2,964,624 rows, 18
  pickups outside `[2024-01-01, 2024-02-01)`, ~37k negative fares/totals, 56 dropoff-before-pickup
  rows, and explicit "unknown" codes for `payment_type=0`, `RatecodeID=99`/null, `VendorID=6`.

## Decisions

- **Typed query spec (`services/dataset_spike/dataset_spike/query_spec.py`)**: closed `DimensionName`,
  `MeasureName` and `FilterField` `StrEnum`s exactly matching the issue's allowlists; `TaxiFilters`,
  `OrderSpec` dataclasses; `QueryValidationError(code, message)` with an `to_envelope()` helper.
  Every validator runs and raises before any DuckDB call — proven by tests that monkeypatch
  `_run_governed_query` to raise `AssertionError` if invoked.
- **SQL compiler (`query_compiler.py`)**: one fixed SQL fragment per enum member; every caller
  value is bound as a DuckDB `?` parameter, never concatenated. `ORDER BY` always includes a
  deterministic tie-breaker over every requested output column.
- **Validity defaults (D8)**: `valid_records_only` defaults to true and applies pickup in
  `[2024-01-01, 2024-02-01)`, `fare_amount >= 0`, `total_amount >= 0`,
  `tpep_dropoff_datetime >= tpep_pickup_datetime`.
- **Tip semantics**: `tip_rate`/`average_tip`/`median_tip`/`total_tips` are computed over
  `payment_type = 1` (card) only and say so in `tip_rate_semantics` metadata, because TLC never
  records cash tips (excluded, not treated as zero).
- **`airport_trip` rule**: true when pickup or dropoff zone is JFK/LaGuardia/Newark Airport, or
  `RatecodeID` is 2 (JFK) or 3 (Newark); documented in `airport_trip_rule` metadata.
- **Bucket dimensions**: fixed, documented edges in `query_spec.py`
  (`PASSENGER_COUNT_BUCKET_EDGES`, `TRIP_DISTANCE_BUCKET_EDGES`, `TRIP_DURATION_BUCKET_EDGES`,
  `FARE_AMOUNT_BUCKET_EDGES`).
- **Four governed functions** in `analytics.py` (`describe_taxi_dataset`,
  `list_taxi_dimension_values`, `aggregate_taxi_data`, `compare_taxi_segments`) all run through the
  existing `_run_governed_query` subprocess/deadline/row-limit machinery; a shared
  `_augment_envelope` helper re-checks the 8 KiB byte budget after adding `query_class` and other
  bounded metadata, truncating rows further if needed. `describe_taxi_dataset` issues two governed
  queries (aggregate stats, `DESCRIBE trips`) and merges them in Python; no additional DuckDB
  surface is exposed.
- **`compare_taxi_segments`**: compiles baseline and comparison into two CTEs of the *same* query,
  `FULL OUTER JOIN`ed on the segment key with one deterministic `ORDER BY` and a single `LIMIT`
  applied after the join, then computes a per-measure delta in Python. This was originally two
  independent, per-side-limited queries reconciled in Python by string-sorted key; that let a
  segment ranked past the limit on only one side show a false NULL/delta (fixed in review, see
  PR #137).
- **MCP tools (`server.py`)**: added `describe_taxi_dataset`, `list_taxi_dimension_values`,
  `aggregate_taxi_data`, `compare_taxi_segments`, each with an injectable runner (for tests) and a
  default pinned runner. A new `_run_governed_analytics_tool` tracing wrapper catches
  `QueryValidationError` and returns the same `{"error": {"code", "message", "retryable": false}}`
  envelope `average_trip_metrics` already used, and sets bounded span attributes
  (`ai.tool.name`, `ai.query_class`, `ai.dimensions`, `ai.measures`, `ai.row_count`,
  `ai.truncated`, `ai.validation_result`, `ai.failure_category`) — never raw SQL or filter values.
  The three existing tools are unchanged.
- **App client (`services/app/app/mcp_client.py`)**: added the four methods to
  `DatasetProfileMCPClient` and `FastMCPDatasetProfileClient`, plus `sanitize_describe_result` and
  `sanitize_governed_query_result`, which allowlist every field (including the new bounded
  `query_class`/`dimensions`/`measures`/`dimension`/`segment_dimension`/`tip_rate_semantics`/
  `airport_trip_rule` metadata) and re-check the byte budget after sanitization. `orchestration/loop.py`
  and `llm.py` were intentionally not touched — Slice C wires the new tools into the agent loop.

## Verification

- `make dataset-test` — 47 passed (`services/dataset_spike/tests/test_governed_analytics.py`
  covering: validation-before-execution for unknown dimension/measure/filter/order-by/limit (each
  asserting the runner is never called), no-caller-string-in-SQL, `describe_taxi_dataset` exact
  date range and code dictionaries, null/invalid summaries, dimension-value listing and
  parameterized search, peak-hours aggregation, payment-type + average-tip semantics,
  `valid_records_only` filtering, the 16-column budget (both `aggregate_taxi_data` and
  `compare_taxi_segments`), the byte envelope, the killable deadline, `compare_taxi_segments`
  rank-safety (a key past a per-side limit still shows correct values on both sides), NULL bucket
  handling, and `airport_trip` three-valued-to-boolean coalescing).
- `make mcp-test` — 20 passed (`services/mcp/tests/test_governed_analytics_tools.py` covering tool
  wiring/injected runners, the structured error envelope, validation failing before DuckDB, and
  bounded telemetry attributes; `test_protocol.py` updated for the 7-tool list).
- `uv run --project services/app pytest services/app/tests -q` — 171 passed
  (`services/app/tests/test_governed_mcp_client.py` covering both sanitizers, a
  reject-before-network-call case, and the `airport_trip` string-label regression).
- Not run: a real-dataset sanity script — `data/nyc-yellow-taxi-2024-01/` does not exist in this
  checkout, so the optional live-data check from the issue was skipped. All facts above come from
  the pinned e1 experiment output (2,964,624 rows; 18 pickups outside `[2024-01-01, 2024-02-01)`;
  ~37k negative fares/totals; 56 dropoff-before-pickup rows).

### Independent review fixes (PR #137, must-fix majors)

A fresh read-only review flagged five mergeability blockers, each fixed with a test that fails
without the fix:

- **`compare_taxi_segments` rank safety**: baseline and comparison are now aggregated in one query
  (two CTEs, `FULL OUTER JOIN`ed on the segment key, one deterministic `ORDER BY`, one `LIMIT`
  after the join) instead of two independently limited/sorted queries unioned and re-sorted by
  `str` in Python — the old approach could silently show a false NULL/delta for a segment ranked
  past the limit on only one side.
- **16-column cap for `compare_taxi_segments`**: `1 + 3 * len(measures)` is now validated against
  `MAX_COLUMNS` before any query runs.
- **NULL bucket handling**: `_bucket_case` now emits `WHEN col IS NULL THEN 'unknown'` first, and
  mid-range labels use half-open interval notation (`[a,b)`) instead of ambiguous `a-b`.
- **`airport_trip` boolean coercion**: every term of the airport-trip expression is wrapped in
  `coalesce(..., false)` so a NULL zone or NULL `RatecodeID` resolves to `false`, not NULL (this
  filter previously dropped ~5% of rows silently).
- **`airport_trip` dimension type**: the dimension now compiles to a `CASE` expression producing
  the string label `'airport'`/`'non_airport'` instead of a raw boolean, so the app's sanitizer
  (which rejects Python `bool` in result rows) no longer rejects every query grouped by this
  dimension.

## Limitations / follow-ups for later slices

- Slice B still owns removing the tool-strip retry and keyword fallback in `ServeLLMClient`.
- Slice C owns wiring these four tools into the ReAct loop, the classifier, and the prefix
  contract; nothing in `orchestration/` or `llm.py` changed here.
- `describe_taxi_dataset`'s null/invalid summary currently covers `passenger_count`, `RatecodeID`,
  `VendorID`, negative fare/total and dropoff-before-pickup counts; it does not enumerate every
  column's null count (the issue asks for "which columns have the most missing values" at the
  dataset-understanding level, answerable from this summary plus the schema list, but a full
  per-column null count was judged out of scope for the byte envelope and left for a follow-up if
  needed).

## PR

Draft PR: `feat(mcp): governed taxi analytics tools (#115 slice A)` — implemented by a Sonnet 5
worker orchestrated by Opus 5.5.

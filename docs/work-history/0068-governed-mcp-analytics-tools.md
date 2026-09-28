# Work history 0065 — Governed MCP analytics tools (#115 slice A)

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
- Dataset facts (experiment e1, pinned Jan 2024 TLC yellow) are documented in
  `.vscode/myfiles/115-react-workload/intent.md` and repeated in the issue: 2,964,624 rows, 18
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
- **`compare_taxi_segments`**: runs baseline and comparison as two independent, structurally
  identical governed queries, then aligns them by segment key in Python and computes a per-measure
  delta — this keeps a single small model from having to reconcile two unrelated result sets itself.
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

- `make dataset-test` — 41 passed (17 new tests in `services/dataset_spike/tests/test_governed_analytics.py`
  covering: validation-before-execution for unknown dimension/measure/filter/order-by/limit (each
  asserting the runner is never called), no-caller-string-in-SQL, `describe_taxi_dataset` exact
  date range and code dictionaries, null/invalid summaries, dimension-value listing and
  parameterized search, peak-hours aggregation, payment-type + average-tip semantics,
  `valid_records_only` filtering, the 16-column budget, the byte envelope, and the killable
  deadline).
- `make mcp-test` — 20 passed (4 new tests in `services/mcp/tests/test_governed_analytics_tools.py`
  covering tool wiring/injected runners, the structured error envelope, validation failing before
  DuckDB, and bounded telemetry attributes; `test_protocol.py` updated for the 7-tool list).
- `uv run --project services/app pytest services/app/tests -q` — 169 passed (9 new tests in
  `services/app/tests/test_governed_mcp_client.py` covering both sanitizers and a
  reject-before-network-call case).
- `ruff check` clean on `services/dataset_spike`, `services/mcp`, `services/app`.
- Not run: a real-dataset sanity script — `data/nyc-yellow-taxi-2024-01/` does not exist in this
  checkout, so the optional live-data check from the issue was skipped. All facts above come from
  the pinned e1 experiment output already recorded in `.vscode/myfiles/115-react-workload/intent.md`.

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

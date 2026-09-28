from __future__ import annotations

import csv
import json
import multiprocessing
import queue
import resource
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from dataset_spike.query_compiler import (
    compile_aggregate_query,
    compile_compare_segments_query,
    compile_dimension_values_query,
)
from dataset_spike.query_spec import (
    PAYMENT_TYPE_CODES,
    RATE_CODE_CODES,
    VENDOR_CODES,
    DimensionName,
    MeasureName,
    build_aggregate_spec,
    validate_dimension,
    validate_filters,
    validate_limit,
    validate_measures,
    validate_search,
)

MAX_QUERY_ROWS = 20
MAX_RESULT_BYTES = 8_192
DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
ALLOWED_ANALYSES = {
    "top_pickup_zones": """
        SELECT z.Zone AS pickup_zone, count(*)::BIGINT AS trip_count
        FROM trips AS t
        JOIN taxi_zones AS z ON t.PULocationID = z.LocationID
        GROUP BY 1
        ORDER BY trip_count DESC, pickup_zone ASC
        LIMIT ?
    """,
    "trip_volume_by_hour": """
        SELECT extract(hour FROM tpep_pickup_datetime)::INTEGER AS pickup_hour,
               count(*)::BIGINT AS trip_count
        FROM trips
        GROUP BY 1
        ORDER BY trip_count DESC, pickup_hour ASC
        LIMIT ?
    """,
    "average_distance_by_weekday": """
        SELECT dayname(tpep_pickup_datetime) AS pickup_weekday,
               round(avg(trip_distance), 2) AS average_trip_distance
        FROM trips
        WHERE trip_distance >= 0
        GROUP BY 1
        ORDER BY min(extract(isodow FROM tpep_pickup_datetime)), pickup_weekday ASC
        LIMIT ?
    """,
}
AVERAGE_TRIP_METRICS_QUERY = """
    SELECT z.Borough AS region_name,
           count(*)::BIGINT AS trip_count,
           round(avg(t.trip_distance), 2) AS average_trip_distance,
           round(avg(t.fare_amount), 2) AS average_fare_amount
    FROM trips AS t
    JOIN taxi_zones AS z ON t.PULocationID = z.LocationID
    WHERE z.Borough NOT IN ('Unknown', 'N/A', 'EWR')
      AND t.trip_distance >= 0
      AND t.fare_amount >= 0
    GROUP BY 1
    ORDER BY trip_count DESC, region_name ASC
    LIMIT ?
"""
AVERAGE_TRIP_METRICS_FOR_REGION_QUERY = """
    SELECT z.Borough AS region_name,
           count(*)::BIGINT AS trip_count,
           round(avg(t.trip_distance), 2) AS average_trip_distance,
           round(avg(t.fare_amount), 2) AS average_fare_amount
    FROM trips AS t
    JOIN taxi_zones AS z ON t.PULocationID = z.LocationID
    WHERE z.Borough NOT IN ('Unknown', 'N/A', 'EWR')
      AND t.trip_distance >= 0
      AND t.fare_amount >= 0
      AND z.Borough = ?
    GROUP BY 1
    ORDER BY trip_count DESC, region_name ASC
    LIMIT ?
"""
NON_BOROUGH_REGIONS = frozenset({"unknown", "n/a", "ewr"})


class RegionValidationError(ValueError):
    """Raised when a requested region is not a governed pickup borough."""


def _resolve_region_name(zone_csv_path: Path, region_name: object) -> str:
    if not isinstance(region_name, str) or not region_name.strip():
        raise RegionValidationError("region_name must be a recognized pickup borough")
    with zone_csv_path.open(newline="", encoding="utf-8") as zone_file:
        boroughs = {
            row["Borough"].strip().casefold(): row["Borough"].strip()
            for row in csv.DictReader(zone_file)
            if row.get("Borough")
            and row["Borough"].strip().casefold() not in NON_BOROUGH_REGIONS
        }
    try:
        return boroughs[region_name.strip().casefold()]
    except KeyError as error:
        raise RegionValidationError(
            "region_name must be a recognized pickup borough"
        ) from error


@dataclass(frozen=True)
class DatasetProfile:
    row_count: int
    zone_row_count: int
    schema_columns: list[str]
    daily_zone_rows: list[dict[str, object]]
    duckdb_settings: dict[str, str]
    timing_ms: int
    rss_bytes: int


def _rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage if sys.platform == "darwin" else usage * 1024


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _execute_governed_query(
    parquet_path: str,
    zone_csv_path: str,
    query: str,
    query_parameters: list[object],
    output_queue: Any,
) -> None:
    """Run DuckDB in a process the parent can terminate at the hard deadline."""
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit = '512MB'")
        connection.execute(
            "CREATE VIEW trips AS SELECT * FROM "
            f"read_parquet('{_sql_path(Path(parquet_path))}')"
        )
        connection.execute(
            "CREATE VIEW taxi_zones AS SELECT * FROM "
            f"read_csv_auto('{_sql_path(Path(zone_csv_path))}', header = true)"
        )
        cursor = connection.execute(query, query_parameters)
        output_queue.put(
            (
                "ok",
                [description[0] for description in cursor.description],
                cursor.fetchall(),
            )
        )
    except BaseException:
        output_queue.put(("error",))
    finally:
        connection.close()


def _run_governed_query(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    query: str,
    query_parameters: list[object],
    result_limit: int,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute a fixed read-only query over pinned local inputs."""
    if isinstance(max_result_bytes, bool) or max_result_bytes < 128:
        raise ValueError("max_result_bytes must be at least 128")
    if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    started = time.perf_counter()
    process_context = multiprocessing.get_context("spawn")
    output_queue = process_context.Queue(maxsize=1)
    worker = process_context.Process(
        target=_execute_governed_query,
        args=(
            str(parquet_path),
            str(zone_csv_path),
            query,
            query_parameters,
            output_queue,
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        worker.terminate()
        worker.join(0.5)
        if worker.is_alive():
            worker.kill()
            worker.join(0.5)
        output_queue.close()
        output_queue.join_thread()
        raise TimeoutError("governed query exceeded its execution deadline")
    try:
        outcome = output_queue.get(timeout=0.5)
    except queue.Empty as error:
        raise RuntimeError("governed query worker returned no result") from error
    finally:
        output_queue.close()
        output_queue.join_thread()
    if outcome[0] != "ok":
        raise RuntimeError("governed query execution failed")
    _, columns, fetched_rows = outcome

    truncated = len(fetched_rows) > result_limit
    rows = [list(row) for row in fetched_rows[:result_limit]]
    result: dict[str, object] = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "execution_duration_ms": round((time.perf_counter() - started) * 1000),
        "query_id": (query_id_factory or (lambda: f"query_{uuid.uuid4().hex}"))(),
        "truncated": truncated,
    }
    while len(json.dumps(result, separators=(",", ":")).encode()) > max_result_bytes:
        if not rows:
            raise ValueError("max_result_bytes is too small for the result envelope")
        rows.pop()
        result["row_count"] = len(rows)
        result["truncated"] = True
    return result


def query_dataset(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    analysis: str,
    limit: int = 5,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute one internally defined read-only analysis over pinned local inputs."""
    if analysis not in ALLOWED_ANALYSES:
        raise ValueError("analysis is not allowlisted")
    if isinstance(limit, bool) or not 1 <= limit <= MAX_QUERY_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
    return _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=ALLOWED_ANALYSES[analysis],
        query_parameters=[limit + 1],
        result_limit=limit,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )


def query_average_trip_metrics(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    region_name: str | None = None,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Compare fixed average trip metrics across governed pickup regions."""
    canonical_region = (
        None
        if region_name is None
        else _resolve_region_name(zone_csv_path, region_name)
    )
    return _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=(
            AVERAGE_TRIP_METRICS_QUERY
            if canonical_region is None
            else AVERAGE_TRIP_METRICS_FOR_REGION_QUERY
        ),
        query_parameters=([6] if canonical_region is None else [canonical_region, 2]),
        result_limit=5 if canonical_region is None else 1,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )


def profile_dataset(
    parquet_path: Path, zone_csv_path: Path, *, top_n: int = 10
) -> DatasetProfile:
    """Run only fixed inspection queries; no caller-supplied SQL reaches DuckDB."""
    if not 1 <= top_n <= 100:
        raise ValueError("top_n must be between 1 and 100")
    started = time.perf_counter()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads = 1")
        connection.execute("SET memory_limit = '512MB'")
        connection.execute(
            f"CREATE VIEW trips AS SELECT * FROM read_parquet('{_sql_path(parquet_path)}')"
        )
        connection.execute(
            "CREATE VIEW taxi_zones AS SELECT * FROM "
            f"read_csv_auto('{_sql_path(zone_csv_path)}', header = true)"
        )
        schema_columns = [
            row[0] for row in connection.execute("DESCRIBE trips").fetchall()
        ]
        row_count = connection.execute("SELECT count(*) FROM trips").fetchone()[0]
        zone_row_count = connection.execute(
            "SELECT count(*) FROM taxi_zones"
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT
                CAST(t.tpep_pickup_datetime AS DATE)::VARCHAR AS pickup_date,
                z.Zone AS pickup_zone,
                count(*)::BIGINT AS trip_count,
                round(sum(t.total_amount), 2) AS total_amount
            FROM trips AS t
            JOIN taxi_zones AS z ON t.PULocationID = z.LocationID
            GROUP BY 1, 2
            ORDER BY trip_count DESC, pickup_date ASC, pickup_zone ASC
            LIMIT ?
            """,
            [top_n],
        ).fetchall()
    finally:
        connection.close()
    return DatasetProfile(
        row_count=row_count,
        zone_row_count=zone_row_count,
        schema_columns=schema_columns,
        daily_zone_rows=[
            {
                "pickup_date": row[0],
                "pickup_zone": row[1],
                "trip_count": row[2],
                "total_amount": row[3],
            }
            for row in rows
        ],
        duckdb_settings={"threads": "1", "memory_limit": "512MB"},
        timing_ms=round((time.perf_counter() - started) * 1000),
        rss_bytes=_rss_bytes(),
    )


# --- Governed analytics tools (issue #115 slice A) --------------------------

_DESCRIBE_BASE_QUERY = """
    SELECT min(t.tpep_pickup_datetime)::VARCHAR AS min_pickup,
           max(t.tpep_pickup_datetime)::VARCHAR AS max_pickup,
           count(*)::BIGINT AS row_count
    FROM trips AS t
"""
_DESCRIBE_WITH_STATS_QUERY = """
    SELECT min(t.tpep_pickup_datetime)::VARCHAR AS min_pickup,
           max(t.tpep_pickup_datetime)::VARCHAR AS max_pickup,
           count(*)::BIGINT AS row_count,
           sum(CASE WHEN t.passenger_count IS NULL THEN 1 ELSE 0 END)::BIGINT
               AS null_passenger_count,
           sum(CASE WHEN t.RatecodeID IS NULL THEN 1 ELSE 0 END)::BIGINT
               AS null_rate_code,
           sum(CASE WHEN t.VendorID IS NULL THEN 1 ELSE 0 END)::BIGINT AS null_vendor,
           sum(CASE WHEN t.fare_amount < 0 THEN 1 ELSE 0 END)::BIGINT
               AS negative_fare_count,
           sum(CASE WHEN t.total_amount < 0 THEN 1 ELSE 0 END)::BIGINT
               AS negative_total_count,
           sum(CASE WHEN t.tpep_dropoff_datetime < t.tpep_pickup_datetime THEN 1 ELSE 0 END)
               ::BIGINT AS dropoff_before_pickup_count
    FROM trips AS t
"""
_DESCRIBE_SCHEMA_QUERY = "DESCRIBE trips"

_TIP_MEASURES = frozenset(
    {
        MeasureName.TIP_RATE,
        MeasureName.AVERAGE_TIP,
        MeasureName.MEDIAN_TIP,
        MeasureName.TOTAL_TIPS,
    }
)
TIP_RATE_SEMANTICS = (
    "tip_rate, average_tip, median_tip and total_tips are computed over card "
    "payments (payment_type = 1) only; TLC does not record cash tips, so cash "
    "trips are excluded rather than treated as zero"
)
AIRPORT_TRIP_RULE = (
    "airport_trip is true when the pickup or dropoff zone is JFK Airport, "
    "LaGuardia Airport or Newark Airport, or when RatecodeID is 2 (JFK) or 3 (Newark)"
)
VALID_RECORDS_RULE = (
    "valid_records_only (default true) keeps pickups in "
    "[2024-01-01, 2024-02-01), fare_amount >= 0, total_amount >= 0 and "
    "tpep_dropoff_datetime >= tpep_pickup_datetime"
)


def _augment_envelope(
    result: dict[str, object], max_result_bytes: int, **extra: object
) -> dict[str, object]:
    merged = dict(result)
    merged.update(extra)
    rows = merged.get("rows")
    while len(json.dumps(merged, separators=(",", ":")).encode()) > max_result_bytes:
        if not isinstance(rows, list) or not rows:
            raise ValueError("max_result_bytes is too small for the result envelope")
        rows.pop()
        merged["row_count"] = len(rows)
        merged["truncated"] = True
    return merged


def describe_taxi_dataset(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    include_column_stats: bool = False,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Return exact dataset bounds, schema, null summary and code dictionaries."""
    if not isinstance(include_column_stats, bool):
        raise ValueError("include_column_stats must be a boolean")

    stats_result = _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=(
            _DESCRIBE_WITH_STATS_QUERY
            if include_column_stats
            else _DESCRIBE_BASE_QUERY
        ),
        query_parameters=[],
        result_limit=1,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )
    schema_result = _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=_DESCRIBE_SCHEMA_QUERY,
        query_parameters=[],
        result_limit=64,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )
    stats_columns = stats_result["columns"]
    stats_row = stats_result["rows"][0] if stats_result["rows"] else []
    stats = dict(zip(stats_columns, stats_row, strict=False))

    columns = [
        {"name": row[0], "type": row[1]} for row in schema_result["rows"]
    ]

    result: dict[str, object] = {
        "query_class": "describe",
        "row_count": stats.get("row_count", 0),
        "min_pickup_datetime": stats.get("min_pickup"),
        "max_pickup_datetime": stats.get("max_pickup"),
        "columns": columns,
        "supported_dimensions": [dimension.value for dimension in DimensionName],
        "supported_measures": [measure.value for measure in MeasureName],
        "code_dictionaries": {
            "payment_type": {str(k): v for k, v in PAYMENT_TYPE_CODES.items()},
            "rate_code": {str(k): v for k, v in RATE_CODE_CODES.items()},
            "vendor": {str(k): v for k, v in VENDOR_CODES.items()},
        },
        "tip_rate_semantics": TIP_RATE_SEMANTICS,
        "airport_trip_rule": AIRPORT_TRIP_RULE,
        "valid_records_rule": VALID_RECORDS_RULE,
        "truncated": False,
        "query_id": stats_result["query_id"],
    }
    if include_column_stats:
        result["null_summary"] = {
            "passenger_count": stats.get("null_passenger_count", 0),
            "rate_code": stats.get("null_rate_code", 0),
            "vendor": stats.get("null_vendor", 0),
        }
        result["invalid_record_summary"] = {
            "negative_fare_count": stats.get("negative_fare_count", 0),
            "negative_total_count": stats.get("negative_total_count", 0),
            "dropoff_before_pickup_count": stats.get(
                "dropoff_before_pickup_count", 0
            ),
        }
    return _augment_envelope(result, max_result_bytes)


def list_taxi_dimension_values(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    dimension: object,
    search: object = None,
    limit: object = 20,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """List bounded, counted values for one allowlisted dimension."""
    validated_dimension = validate_dimension(dimension)
    validated_search = validate_search(search)
    validated_limit = validate_limit(limit)

    compiled = compile_dimension_values_query(
        validated_dimension, search=validated_search, limit=validated_limit
    )
    result = _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=compiled.sql,
        query_parameters=compiled.parameters,
        result_limit=validated_limit,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )
    return _augment_envelope(
        result,
        max_result_bytes,
        query_class="dimension_values",
        dimension=validated_dimension.value,
    )


def aggregate_taxi_data(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    dimensions: object,
    measures: object,
    filters: object = None,
    order_by: object = None,
    limit: object = 20,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Run the primary flexible aggregation over the governed taxi surface."""
    spec = build_aggregate_spec(
        dimensions=dimensions,
        measures=measures,
        filters=filters,
        order_by=order_by,
        limit=limit,
    )

    compiled = compile_aggregate_query(spec)
    result = _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=compiled.sql,
        query_parameters=compiled.parameters,
        result_limit=spec.limit,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )
    extra: dict[str, object] = {
        "query_class": "aggregate",
        "dimensions": [dimension.value for dimension in spec.dimensions],
        "measures": [measure.value for measure in spec.measures],
    }
    if any(measure in _TIP_MEASURES for measure in spec.measures):
        extra["tip_rate_semantics"] = TIP_RATE_SEMANTICS
    if DimensionName.AIRPORT_TRIP in spec.dimensions or (
        spec.filters is not None and spec.filters.airport_trip is not None
    ):
        extra["airport_trip_rule"] = AIRPORT_TRIP_RULE
    return _augment_envelope(result, max_result_bytes, **extra)


def compare_taxi_segments(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    segment_dimension: object,
    measures: object,
    baseline_filters: object,
    comparison_filters: object,
    limit: object = 20,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Align baseline vs comparison segments for the same dimension/measures."""
    validated_dimension = validate_dimension(segment_dimension)
    validated_measures = validate_measures(measures)
    validated_limit = validate_limit(limit)
    validated_baseline = validate_filters(baseline_filters)
    validated_comparison = validate_filters(comparison_filters)

    baseline_compiled, comparison_compiled = compile_compare_segments_query(
        segment_dimension=validated_dimension,
        measures=validated_measures,
        baseline_filters=validated_baseline,
        comparison_filters=validated_comparison,
        limit=validated_limit,
    )
    baseline_result = _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=baseline_compiled.sql,
        query_parameters=baseline_compiled.parameters,
        result_limit=validated_limit,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )
    comparison_result = _run_governed_query(
        parquet_path,
        zone_csv_path,
        query=comparison_compiled.sql,
        query_parameters=comparison_compiled.parameters,
        result_limit=validated_limit,
        max_result_bytes=max_result_bytes,
        timeout_seconds=timeout_seconds,
        query_id_factory=query_id_factory,
    )

    baseline_map = {row[0]: row[1:] for row in baseline_result["rows"]}
    comparison_map = {row[0]: row[1:] for row in comparison_result["rows"]}
    all_keys = sorted(set(baseline_map) | set(comparison_map), key=str)
    truncated = bool(
        baseline_result["truncated"]
        or comparison_result["truncated"]
        or len(all_keys) > validated_limit
    )
    limited_keys = all_keys[:validated_limit]

    columns: list[str] = [validated_dimension.value]
    for measure in validated_measures:
        columns += [
            f"baseline_{measure.value}",
            f"comparison_{measure.value}",
            f"delta_{measure.value}",
        ]

    rows: list[list[object]] = []
    for key in limited_keys:
        baseline_values = baseline_map.get(key)
        comparison_values = comparison_map.get(key)
        row: list[object] = [key]
        for index in range(len(validated_measures)):
            baseline_value = baseline_values[index] if baseline_values else None
            comparison_value = (
                comparison_values[index] if comparison_values else None
            )
            delta: object = None
            if isinstance(baseline_value, (int, float)) and not isinstance(
                baseline_value, bool
            ) and isinstance(comparison_value, (int, float)) and not isinstance(
                comparison_value, bool
            ):
                delta = round(comparison_value - baseline_value, 4)
            row.extend([baseline_value, comparison_value, delta])
        rows.append(row)

    result: dict[str, object] = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "execution_duration_ms": (
            baseline_result["execution_duration_ms"]
            + comparison_result["execution_duration_ms"]
        ),
        "query_id": (query_id_factory or (lambda: f"query_{uuid.uuid4().hex}"))(),
        "truncated": truncated,
        "query_class": "compare_segments",
        "segment_dimension": validated_dimension.value,
        "measures": [measure.value for measure in validated_measures],
    }
    if any(measure in _TIP_MEASURES for measure in validated_measures):
        result["tip_rate_semantics"] = TIP_RATE_SEMANTICS
    return _augment_envelope(result, max_result_bytes)

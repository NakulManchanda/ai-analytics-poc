"""Compile a validated ``AggregateQuerySpec`` into parameterised DuckDB SQL.

Every SQL fragment below is a fixed string keyed by enum member. No caller
string is ever concatenated into SQL text; all caller-supplied values are
bound through DuckDB's ``?`` parameters. This module never sees raw request
payloads -- only already-validated dataclasses from ``query_spec``.
"""

from __future__ import annotations

from dataclasses import dataclass

from dataset_spike.query_spec import (
    FARE_AMOUNT_BUCKET_EDGES,
    PASSENGER_COUNT_BUCKET_EDGES,
    TRIP_DISTANCE_BUCKET_EDGES,
    TRIP_DURATION_BUCKET_EDGES,
    AggregateQuerySpec,
    DimensionName,
    MeasureName,
    OrderSpec,
    TaxiFilters,
)

# --- fixed per-dimension SQL fragments (select expr, group-by key) ---------


def _bucket_case(column: str, edges: tuple[float, ...]) -> str:
    branches = []
    previous = None
    for edge in edges:
        if previous is None:
            branches.append(f"WHEN {column} < {edge} THEN '<{edge}'")
        else:
            branches.append(f"WHEN {column} < {edge} THEN '{previous}-{edge}'")
        previous = edge
    branches.append(f"ELSE '{previous}+'")
    return "CASE " + " ".join(branches) + " END"


_DIMENSION_EXPRESSIONS: dict[DimensionName, str] = {
    DimensionName.PICKUP_DATE: "CAST(t.tpep_pickup_datetime AS DATE)::VARCHAR",
    DimensionName.PICKUP_HOUR: "extract(hour FROM t.tpep_pickup_datetime)::INTEGER",
    DimensionName.PICKUP_WEEKDAY: "dayname(t.tpep_pickup_datetime)",
    DimensionName.PICKUP_WEEK_PART: (
        "CASE WHEN extract(isodow FROM t.tpep_pickup_datetime) IN (6, 7) "
        "THEN 'weekend' ELSE 'weekday' END"
    ),
    DimensionName.PICKUP_ZONE: "pz.Zone",
    DimensionName.PICKUP_BOROUGH: "pz.Borough",
    DimensionName.DROPOFF_ZONE: "dz.Zone",
    DimensionName.DROPOFF_BOROUGH: "dz.Borough",
    DimensionName.PAYMENT_TYPE: "t.payment_type::INTEGER",
    DimensionName.RATE_CODE: "t.RatecodeID::INTEGER",
    DimensionName.VENDOR: "t.VendorID::INTEGER",
    DimensionName.PASSENGER_COUNT_BUCKET: _bucket_case(
        "t.passenger_count", PASSENGER_COUNT_BUCKET_EDGES
    ),
    DimensionName.TRIP_DISTANCE_BUCKET: _bucket_case(
        "t.trip_distance", TRIP_DISTANCE_BUCKET_EDGES
    ),
    DimensionName.TRIP_DURATION_BUCKET: _bucket_case(
        "date_diff('minute', t.tpep_pickup_datetime, t.tpep_dropoff_datetime)",
        TRIP_DURATION_BUCKET_EDGES,
    ),
    DimensionName.FARE_AMOUNT_BUCKET: _bucket_case(
        "t.fare_amount", FARE_AMOUNT_BUCKET_EDGES
    ),
    DimensionName.AIRPORT_TRIP: (
        "(pz.Zone IN ('JFK Airport', 'LaGuardia Airport', 'Newark Airport') "
        "OR dz.Zone IN ('JFK Airport', 'LaGuardia Airport', 'Newark Airport') "
        "OR t.RatecodeID IN (2, 3))"
    ),
}

_DURATION_MINUTES_EXPR = (
    "date_diff('minute', t.tpep_pickup_datetime, t.tpep_dropoff_datetime)"
)

_MEASURE_EXPRESSIONS: dict[MeasureName, str] = {
    MeasureName.TRIP_COUNT: "count(*)::BIGINT",
    MeasureName.TRIP_SHARE: (
        "round(count(*)::DOUBLE / sum(count(*)::DOUBLE) OVER (), 4)"
    ),
    MeasureName.AVERAGE_FARE: "round(avg(t.fare_amount), 2)",
    MeasureName.MEDIAN_FARE: "round(median(t.fare_amount), 2)",
    MeasureName.AVERAGE_TIP: (
        "round(avg(CASE WHEN t.payment_type = 1 THEN t.tip_amount END), 2)"
    ),
    MeasureName.MEDIAN_TIP: (
        "round(median(CASE WHEN t.payment_type = 1 THEN t.tip_amount END), 2)"
    ),
    MeasureName.TOTAL_TIPS: (
        "round(sum(CASE WHEN t.payment_type = 1 THEN t.tip_amount ELSE 0 END), 2)"
    ),
    # Card-only tip rate: TLC never records cash tips, so they are excluded
    # (not treated as zero). Documented in describe_taxi_dataset metadata.
    MeasureName.TIP_RATE: (
        "round(sum(CASE WHEN t.payment_type = 1 THEN t.tip_amount ELSE 0 END) / "
        "NULLIF(sum(CASE WHEN t.payment_type = 1 THEN t.fare_amount ELSE 0 END), 0), 4)"
    ),
    MeasureName.AVERAGE_TOTAL_AMOUNT: "round(avg(t.total_amount), 2)",
    MeasureName.AVERAGE_DISTANCE: "round(avg(t.trip_distance), 2)",
    MeasureName.MEDIAN_DISTANCE: "round(median(t.trip_distance), 2)",
    MeasureName.AVERAGE_DURATION_MINUTES: f"round(avg({_DURATION_MINUTES_EXPR}), 2)",
    MeasureName.MEDIAN_DURATION_MINUTES: f"round(median({_DURATION_MINUTES_EXPR}), 2)",
    MeasureName.P95_DURATION_MINUTES: (
        f"round(quantile_cont({_DURATION_MINUTES_EXPR}, 0.95), 2)"
    ),
    MeasureName.AVERAGE_PASSENGER_COUNT: "round(avg(t.passenger_count), 2)",
}

_ZONE_JOIN_DIMENSIONS = frozenset(
    {
        DimensionName.PICKUP_ZONE,
        DimensionName.PICKUP_BOROUGH,
        DimensionName.DROPOFF_ZONE,
        DimensionName.DROPOFF_BOROUGH,
        DimensionName.AIRPORT_TRIP,
    }
)


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: list[object]
    output_columns: list[str]


def _requires_zone_join(dimensions: list[DimensionName], filters: TaxiFilters | None) -> bool:
    if any(dimension in _ZONE_JOIN_DIMENSIONS for dimension in dimensions):
        return True
    return bool(filters and filters.uses_zone_join())


def _from_clause(zone_join: bool) -> str:
    if zone_join:
        return (
            "FROM trips AS t "
            "LEFT JOIN taxi_zones AS pz ON t.PULocationID = pz.LocationID "
            "LEFT JOIN taxi_zones AS dz ON t.DOLocationID = dz.LocationID"
        )
    return "FROM trips AS t"


def _compile_filters(
    filters: TaxiFilters | None, zone_join: bool
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if filters is None:
        filters = TaxiFilters()

    if filters.valid_records_only:
        clauses.append(
            "t.tpep_pickup_datetime >= TIMESTAMP '2024-01-01' "
            "AND t.tpep_pickup_datetime < TIMESTAMP '2024-02-01'"
        )
        clauses.append("t.fare_amount >= 0")
        clauses.append("t.total_amount >= 0")
        clauses.append("t.tpep_dropoff_datetime >= t.tpep_pickup_datetime")

    if filters.start_timestamp is not None:
        clauses.append("t.tpep_pickup_datetime >= ?")
        parameters.append(filters.start_timestamp)
    if filters.end_timestamp is not None:
        clauses.append("t.tpep_pickup_datetime < ?")
        parameters.append(filters.end_timestamp)
    if filters.pickup_hour_min is not None:
        clauses.append("extract(hour FROM t.tpep_pickup_datetime) >= ?")
        parameters.append(filters.pickup_hour_min)
    if filters.pickup_hour_max is not None:
        clauses.append("extract(hour FROM t.tpep_pickup_datetime) <= ?")
        parameters.append(filters.pickup_hour_max)
    if filters.pickup_weekday is not None:
        clauses.append("dayname(t.tpep_pickup_datetime) = ?")
        parameters.append(filters.pickup_weekday)
    if filters.pickup_week_part is not None:
        if filters.pickup_week_part == "weekend":
            clauses.append("extract(isodow FROM t.tpep_pickup_datetime) IN (6, 7)")
        else:
            clauses.append("extract(isodow FROM t.tpep_pickup_datetime) NOT IN (6, 7)")
    if filters.pickup_zone is not None:
        clauses.append("lower(pz.Zone) = lower(?)")
        parameters.append(filters.pickup_zone)
    if filters.pickup_borough is not None:
        clauses.append("lower(pz.Borough) = lower(?)")
        parameters.append(filters.pickup_borough)
    if filters.dropoff_zone is not None:
        clauses.append("lower(dz.Zone) = lower(?)")
        parameters.append(filters.dropoff_zone)
    if filters.dropoff_borough is not None:
        clauses.append("lower(dz.Borough) = lower(?)")
        parameters.append(filters.dropoff_borough)
    if filters.payment_type is not None:
        clauses.append("t.payment_type = ?")
        parameters.append(filters.payment_type)
    if filters.rate_code is not None:
        clauses.append("t.RatecodeID = ?")
        parameters.append(filters.rate_code)
    if filters.vendor is not None:
        clauses.append("t.VendorID = ?")
        parameters.append(filters.vendor)
    if filters.passenger_count_min is not None:
        clauses.append("t.passenger_count >= ?")
        parameters.append(filters.passenger_count_min)
    if filters.passenger_count_max is not None:
        clauses.append("t.passenger_count <= ?")
        parameters.append(filters.passenger_count_max)
    if filters.trip_distance_min is not None:
        clauses.append("t.trip_distance >= ?")
        parameters.append(filters.trip_distance_min)
    if filters.trip_distance_max is not None:
        clauses.append("t.trip_distance <= ?")
        parameters.append(filters.trip_distance_max)
    if filters.trip_duration_min is not None:
        clauses.append(f"{_DURATION_MINUTES_EXPR} >= ?")
        parameters.append(filters.trip_duration_min)
    if filters.trip_duration_max is not None:
        clauses.append(f"{_DURATION_MINUTES_EXPR} <= ?")
        parameters.append(filters.trip_duration_max)
    if filters.fare_amount_min is not None:
        clauses.append("t.fare_amount >= ?")
        parameters.append(filters.fare_amount_min)
    if filters.fare_amount_max is not None:
        clauses.append("t.fare_amount <= ?")
        parameters.append(filters.fare_amount_max)
    if filters.airport_trip is not None:
        airport_expr = _DIMENSION_EXPRESSIONS[DimensionName.AIRPORT_TRIP]
        clauses.append(f"{airport_expr} = ?")
        parameters.append(filters.airport_trip)

    return clauses, parameters


def _order_clause(
    order_by: OrderSpec | None,
    dimensions: list[DimensionName],
    measures: list[MeasureName],
) -> str:
    # Deterministic ORDER BY with a tie-breaker on every output column.
    output_keys = [dimension.value for dimension in dimensions] + [
        measure.value for measure in measures
    ]
    if order_by is not None:
        direction = "DESC" if order_by.direction == "desc" else "ASC"
        primary = f'"{order_by.key}" {direction}'
        tie_breakers = [f'"{key}" ASC' for key in output_keys if key != order_by.key]
    else:
        primary = None
        tie_breakers = [f'"{key}" ASC' for key in output_keys]
    parts = ([primary] if primary else []) + tie_breakers
    return "ORDER BY " + ", ".join(parts) if parts else ""


def compile_aggregate_query(spec: AggregateQuerySpec) -> CompiledQuery:
    zone_join = _requires_zone_join(spec.dimensions, spec.filters)
    select_parts = [
        f'{_DIMENSION_EXPRESSIONS[dimension]} AS "{dimension.value}"'
        for dimension in spec.dimensions
    ] + [
        f'{_MEASURE_EXPRESSIONS[measure]} AS "{measure.value}"' for measure in spec.measures
    ]
    output_columns = [dimension.value for dimension in spec.dimensions] + [
        measure.value for measure in spec.measures
    ]
    group_by = ", ".join(str(i + 1) for i in range(len(spec.dimensions)))

    where_clauses, parameters = _compile_filters(spec.filters, zone_join)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    group_sql = f"GROUP BY {group_by}" if group_by else ""
    order_sql = _order_clause(spec.order_by, spec.dimensions, spec.measures)

    sql = " ".join(
        part
        for part in (
            f"SELECT {', '.join(select_parts)}",
            _from_clause(zone_join),
            where_sql,
            group_sql,
            order_sql,
            "LIMIT ?",
        )
        if part
    )
    parameters = list(parameters) + [spec.limit + 1]
    return CompiledQuery(sql=sql, parameters=parameters, output_columns=output_columns)


def compile_dimension_values_query(
    dimension: DimensionName, *, search: str | None, limit: int
) -> CompiledQuery:
    zone_join = dimension in _ZONE_JOIN_DIMENSIONS
    expr = _DIMENSION_EXPRESSIONS[dimension]
    where_clauses = ["t.tpep_pickup_datetime >= TIMESTAMP '2024-01-01'",
                      "t.tpep_pickup_datetime < TIMESTAMP '2024-02-01'"]
    parameters: list[object] = []
    if search is not None:
        where_clauses.append(f"lower(CAST({expr} AS VARCHAR)) LIKE lower(?)")
        parameters.append(f"%{search}%")
    where_sql = f"WHERE {' AND '.join(where_clauses)}"
    sql = " ".join(
        (
            f'SELECT {expr} AS "value", count(*)::BIGINT AS "trip_count"',
            _from_clause(zone_join),
            where_sql,
            'GROUP BY 1',
            'ORDER BY "trip_count" DESC, "value" ASC',
            "LIMIT ?",
        )
    )
    parameters.append(limit + 1)
    return CompiledQuery(sql=sql, parameters=parameters, output_columns=["value", "trip_count"])


def compile_compare_segments_query(
    *,
    segment_dimension: DimensionName,
    measures: list[MeasureName],
    baseline_filters: TaxiFilters,
    comparison_filters: TaxiFilters,
    limit: int,
) -> tuple[CompiledQuery, CompiledQuery]:
    """Two structurally identical queries (baseline, comparison) sharing dimensions/measures."""
    baseline_spec = AggregateQuerySpec(
        dimensions=[segment_dimension],
        measures=measures,
        filters=baseline_filters,
        order_by=None,
        limit=limit,
    )
    comparison_spec = AggregateQuerySpec(
        dimensions=[segment_dimension],
        measures=measures,
        filters=comparison_filters,
        order_by=None,
        limit=limit,
    )
    return compile_aggregate_query(baseline_spec), compile_aggregate_query(comparison_spec)

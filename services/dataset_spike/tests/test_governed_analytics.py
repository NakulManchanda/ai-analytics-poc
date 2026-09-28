from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dataset_spike.query_spec import QueryValidationError


def write_analytics_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A small synthetic parquet + zone CSV fixture covering every governed field."""
    parquet = tmp_path / "yellow.parquet"
    zones = tmp_path / "zones.csv"
    connection = duckdb.connect()
    connection.execute(
        """
        COPY (
            SELECT * FROM (VALUES
                -- weekday Mon 2024-01-01, Manhattan -> Manhattan, card, standard, vendor 1
                (TIMESTAMP '2024-01-01 08:00:00', TIMESTAMP '2024-01-01 08:10:00',
                 1, 4, 1, 1.0, 2.0, 2.0, 10.0, 12.0, 1, 1),
                -- weekday Mon 2024-01-01, Manhattan -> JFK, card, JFK rate, vendor 1
                (TIMESTAMP '2024-01-01 09:00:00', TIMESTAMP '2024-01-01 09:40:00',
                 1, 132, 1, 1.0, 20.0, 5.0, 40.0, 45.0, 1, 2),
                -- weekend Sat 2024-01-06, Bronx -> Bronx, cash, standard, vendor 2
                (TIMESTAMP '2024-01-06 17:00:00', TIMESTAMP '2024-01-06 17:20:00',
                 2, 2, 2, 2.0, 8.0, 0.0, 16.0, 16.0, 2, 1),
                -- weekend Sun 2024-01-07, Bronx -> Manhattan, cash, standard, vendor 2
                (TIMESTAMP '2024-01-07 18:00:00', TIMESTAMP '2024-01-07 18:30:00',
                 2, 1, 2, 3.0, 6.0, 0.0, 12.0, 12.0, 2, 1),
                -- weekday Tue 2024-01-02, invalid negative fare (excluded by default)
                (TIMESTAMP '2024-01-02 10:00:00', TIMESTAMP '2024-01-02 10:05:00',
                 1, 1, 1, 1.0, 1.0, 0.0, -5.0, -5.0, 1, 1)
            ) AS trips(
                tpep_pickup_datetime, tpep_dropoff_datetime,
                PULocationID, DOLocationID, payment_type, passenger_count,
                trip_distance, tip_amount, fare_amount, total_amount,
                VendorID, RatecodeID
            )
        ) TO ? (FORMAT PARQUET)
        """,
        [str(parquet)],
    )
    connection.close()
    zones.write_text(
        "LocationID,Borough,Zone,service_zone\n"
        "1,Manhattan,Alpha,Boro Zone\n"
        "2,Bronx,Beta,Boro Zone\n"
        "132,Queens,JFK Airport,Airports\n"
    )
    return parquet, zones


class TestValidationBeforeExecution:
    """Unknown/malformed inputs must fail before touching DuckDB or the runner."""

    def test_unknown_dimension_is_rejected_before_the_runner_is_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)

        def fail_if_called(*args: object, **kwargs: object) -> None:
            raise AssertionError("_run_governed_query must not be called")

        monkeypatch.setattr(analytics, "_run_governed_query", fail_if_called)

        with pytest.raises(QueryValidationError) as excinfo:
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["not_a_real_dimension"],
                measures=["trip_count"],
            )
        assert excinfo.value.code == "unknown_dimension"

    def test_unknown_measure_is_rejected_before_the_runner_is_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        monkeypatch.setattr(
            analytics,
            "_run_governed_query",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["pickup_hour"],
                measures=["not_a_real_measure"],
            )
        assert excinfo.value.code == "unknown_measure"

    def test_unknown_filter_field_is_rejected(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["pickup_hour"],
                measures=["trip_count"],
                filters={"not_a_real_filter": 1},
            )
        assert excinfo.value.code == "unknown_filter"

    def test_malformed_filter_value_is_rejected(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["pickup_hour"],
                measures=["trip_count"],
                filters={"payment_type": 42},
            )
        assert excinfo.value.code == "invalid_filter_value"

    def test_unknown_dimension_for_list_values_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        with pytest.raises(QueryValidationError):
            analytics.list_taxi_dimension_values(parquet, zones, dimension="not_real")

    def test_order_by_key_outside_the_requested_columns_is_rejected(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["pickup_hour"],
                measures=["trip_count"],
                order_by={"key": "average_fare"},
            )
        assert excinfo.value.code == "invalid_order_by"

    def test_limit_out_of_range_is_rejected(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        with pytest.raises(QueryValidationError):
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["pickup_hour"],
                measures=["trip_count"],
                limit=21,
            )


class TestNoCallerStringInSQL:
    def test_filter_string_values_never_appear_in_compiled_sql_text(self) -> None:
        from dataset_spike.query_compiler import compile_aggregate_query
        from dataset_spike.query_spec import build_aggregate_spec

        secret_zone = "zzz_caller_supplied_zone_marker"
        spec = build_aggregate_spec(
            dimensions=["pickup_borough"],
            measures=["trip_count"],
            filters={"pickup_zone": secret_zone},
            order_by=None,
            limit=5,
        )
        compiled = compile_aggregate_query(spec)
        assert secret_zone not in compiled.sql
        assert secret_zone in compiled.parameters


class TestDescribeTaxiDataset:
    def test_describe_returns_the_exact_pickup_range_and_dictionaries(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.describe_taxi_dataset(
            parquet, zones, query_id_factory=lambda: "query_describe"
        )
        assert result["min_pickup_datetime"] == "2024-01-01 08:00:00"
        assert result["max_pickup_datetime"] == "2024-01-07 18:00:00"
        assert result["row_count"] == 5
        assert "payment_type" in result["code_dictionaries"]
        assert (
            result["code_dictionaries"]["payment_type"]["0"] == "unknown_or_flex_fare"
        )
        assert "pickup_hour" in result["supported_dimensions"]
        assert "tip_rate" in result["supported_measures"]
        assert result["truncated"] is False

    def test_describe_with_column_stats_reports_null_and_invalid_summaries(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.describe_taxi_dataset(
            parquet, zones, include_column_stats=True
        )
        assert result["null_summary"]["passenger_count"] == 0
        assert result["invalid_record_summary"]["negative_fare_count"] == 1


class TestListTaxiDimensionValues:
    def test_lists_bounded_values_with_counts(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.list_taxi_dimension_values(
            parquet, zones, dimension="pickup_borough", limit=10
        )
        assert result["columns"] == ["value", "trip_count"]
        boroughs = {row[0] for row in result["rows"]}
        assert "Manhattan" in boroughs
        assert result["truncated"] is False

    def test_search_is_parameterized_and_case_insensitive(self, tmp_path: Path) -> None:
        from dataset_spike import analytics
        from dataset_spike.query_compiler import compile_dimension_values_query
        from dataset_spike.query_spec import DimensionName

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.list_taxi_dimension_values(
            parquet, zones, dimension="pickup_borough", search="man"
        )
        assert all(row[0] == "Manhattan" for row in result["rows"])

        compiled = compile_dimension_values_query(
            DimensionName.PICKUP_BOROUGH, search="man", limit=10
        )
        assert "man" not in compiled.sql
        assert "%man%" in compiled.parameters


class TestAggregateTaxiData:
    def test_peak_hours_aggregation_returns_hours_not_regions(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["pickup_hour"],
            measures=["trip_count"],
            order_by={"key": "trip_count", "direction": "desc"},
        )
        assert result["columns"] == ["pickup_hour", "trip_count"]
        assert all(isinstance(row[0], int) for row in result["rows"])
        assert result["query_class"] == "aggregate"

    def test_payment_counts_and_average_tip_include_code_semantics(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["payment_type"],
            measures=["trip_count", "average_tip"],
        )
        assert "tip_rate_semantics" in result
        payment_types = {row[0] for row in result["rows"]}
        assert payment_types <= {1, 2}

    def test_valid_records_only_excludes_negative_fares_by_default(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["vendor"],
            measures=["trip_count"],
        )
        total = sum(row[-1] for row in result["rows"])
        assert total == 4  # the 5th synthetic row has fare_amount = -5.0

    def test_column_budget_is_enforced(self, tmp_path: Path) -> None:
        from dataset_spike import analytics
        from dataset_spike.query_spec import DimensionName, MeasureName

        parquet, zones = write_analytics_fixture(tmp_path)
        dimensions = [d.value for d in DimensionName]  # 16 values
        measures = [MeasureName.TRIP_COUNT.value]
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.aggregate_taxi_data(
                parquet, zones, dimensions=dimensions, measures=measures
            )
        assert excinfo.value.code == "invalid_column_budget"


class TestCompareTaxiSegments:
    def test_compares_weekday_and_weekend_demand(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.compare_taxi_segments(
            parquet,
            zones,
            segment_dimension="pickup_borough",
            measures=["trip_count"],
            baseline_filters={"pickup_week_part": "weekday"},
            comparison_filters={"pickup_week_part": "weekend"},
        )
        assert result["columns"] == [
            "pickup_borough",
            "baseline_trip_count",
            "comparison_trip_count",
            "delta_trip_count",
        ]
        assert result["query_class"] == "compare_segments"

    def test_compares_manhattan_and_queens_trip_volume(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.compare_taxi_segments(
            parquet,
            zones,
            segment_dimension="pickup_hour",
            measures=["trip_count"],
            baseline_filters={"pickup_borough": "Manhattan"},
            comparison_filters={"pickup_borough": "Bronx"},
        )
        assert result["row_count"] >= 1


class TestEnvelopeLimits:
    def test_aggregate_result_stays_within_the_byte_envelope(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["pickup_hour"],
            measures=["trip_count"],
            max_result_bytes=300,
        )
        import json

        assert len(json.dumps(result, separators=(",", ":")).encode()) <= 300

    def test_governed_query_deadline_is_enforced_for_aggregate_taxi_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dataset_spike.query_compiler as compiler

        parquet, zones = write_analytics_fixture(tmp_path)

        def slow_query(spec):  # noqa: ANN001
            from dataset_spike.query_compiler import CompiledQuery

            return CompiledQuery(
                sql="SELECT sum(i) AS total FROM range(1000000000000) AS values(i) LIMIT ?",
                parameters=[spec.limit + 1],
                output_columns=["total"],
            )

        monkeypatch.setattr(compiler, "compile_aggregate_query", slow_query)
        import dataset_spike.analytics as analytics

        monkeypatch.setattr(analytics, "compile_aggregate_query", slow_query)

        with pytest.raises(TimeoutError):
            analytics.aggregate_taxi_data(
                parquet,
                zones,
                dimensions=["pickup_hour"],
                measures=["trip_count"],
                timeout_seconds=0.01,
            )


def write_compare_rank_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A pickup hour whose per-side LIMIT position differs from its rank.

    Under the OLD (pre-single-query) compare implementation, each side ran
    its own ``GROUP BY pickup_hour ... LIMIT`` independently, and with no
    explicit ``order_by`` the compiler's default tie-breaker ordering is
    dimension ASC (see ``_order_clause``) -- i.e. purely by hour number, not
    by trip_count. Hour 3 has the largest baseline count (50) but is the
    *numerically last* of the three hours present on the baseline side
    (1, 2, 3), so an ASC-ordered per-side ``LIMIT 2`` cuts it from the
    baseline side while it survives (trivially, as the smallest present
    hour) on the comparison side. A correct single combined-query
    implementation ranks by combined trip_count and keeps hour 3 --
    which dominates -- with its real (non-NULL) baseline value.
    """
    parquet = tmp_path / "yellow.parquet"
    zones = tmp_path / "zones.csv"

    def rows_for(hour: int, payment_type: int, count: int) -> list[str]:
        return [
            f"(TIMESTAMP '2024-01-01 {hour:02d}:00:00', "
            f"TIMESTAMP '2024-01-01 {hour:02d}:10:00', "
            f"1, 1, {payment_type}, 1.0, 1.0, 0.0, 10.0, 10.0, 1, 1)"
            for _ in range(count)
        ]

    values: list[str] = []
    values += rows_for(1, 1, 1)  # hour 1, baseline (payment_type=1): 1
    values += rows_for(2, 1, 1)  # hour 2, baseline: 1
    values += rows_for(3, 1, 50)  # hour 3, baseline: 50 (largest, but ASC-last)
    values += rows_for(3, 2, 50)  # hour 3, comparison (payment_type=2): 50
    values += rows_for(4, 2, 1)  # hour 4, comparison: 1

    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT * FROM (VALUES " + ", ".join(values) + ") AS trips("
        "tpep_pickup_datetime, tpep_dropoff_datetime, PULocationID, DOLocationID, "
        "payment_type, passenger_count, trip_distance, tip_amount, fare_amount, "
        "total_amount, VendorID, RatecodeID)"
        ") TO ? (FORMAT PARQUET)",
        [str(parquet)],
    )
    connection.close()
    zones.write_text(
        "LocationID,Borough,Zone,service_zone\n1,Manhattan,ZoneA,Boro Zone\n"
    )
    return parquet, zones


class TestCompareTaxiSegmentsRankSafety:
    """M1: a key present on both sides must not be dropped by per-side LIMIT."""

    def test_key_ranked_past_limit_on_one_side_still_shows_correct_values(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_compare_rank_fixture(tmp_path)
        result = analytics.compare_taxi_segments(
            parquet,
            zones,
            segment_dimension="pickup_hour",
            measures=["trip_count"],
            baseline_filters={"payment_type": 1},
            comparison_filters={"payment_type": 2},
            limit=2,
        )
        rows_by_hour = {row[0]: row for row in result["rows"]}
        assert 3 in rows_by_hour, (
            "hour 3 has combined trip_count 100 (highest) so it must survive "
            f"the combined LIMIT; got rows: {result['rows']}"
        )
        hour_row = rows_by_hour[3]
        assert hour_row[1] == 50  # baseline_trip_count, not NULL
        assert hour_row[2] == 50  # comparison_trip_count, not NULL
        assert hour_row[3] == 0  # delta_trip_count


def write_compare_null_key_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Rows with a NULL RatecodeID on both baseline and comparison sides.

    The FULL OUTER JOIN predicate must treat NULL rate_code as a single
    matching key across baseline and comparison, not split it into two
    half-empty rows (NULL = NULL is NULL/false in SQL).
    """
    parquet = tmp_path / "yellow.parquet"
    zones = tmp_path / "zones.csv"

    def rows_for(rate_code: str, payment_type: int, count: int) -> list[str]:
        return [
            "(TIMESTAMP '2024-01-01 08:00:00', TIMESTAMP '2024-01-01 08:10:00', "
            f"1, 1, {payment_type}, 1.0, 1.0, 0.0, 10.0, 10.0, 1, {rate_code})"
            for _ in range(count)
        ]

    values: list[str] = []
    values += rows_for("1", 1, 5)  # RatecodeID=1, baseline (payment_type=1): 5
    values += rows_for("1", 2, 3)  # RatecodeID=1, comparison (payment_type=2): 3
    values += rows_for("NULL", 1, 4)  # RatecodeID=NULL, baseline: 4
    values += rows_for("NULL", 2, 2)  # RatecodeID=NULL, comparison: 2

    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT * FROM (VALUES " + ", ".join(values) + ") AS trips("
        "tpep_pickup_datetime, tpep_dropoff_datetime, PULocationID, DOLocationID, "
        "payment_type, passenger_count, trip_distance, tip_amount, fare_amount, "
        "total_amount, VendorID, RatecodeID)"
        ") TO ? (FORMAT PARQUET)",
        [str(parquet)],
    )
    connection.close()
    zones.write_text(
        "LocationID,Borough,Zone,service_zone\n1,Manhattan,ZoneA,Boro Zone\n"
    )
    return parquet, zones


class TestCompareTaxiSegmentsNullSafeJoin:
    """N1: a NULL segment key must merge into one row, not split in two."""

    def test_null_rate_code_merges_into_a_single_row(self, tmp_path: Path) -> None:
        from dataset_spike import analytics

        parquet, zones = write_compare_null_key_fixture(tmp_path)
        result = analytics.compare_taxi_segments(
            parquet,
            zones,
            segment_dimension="rate_code",
            measures=["trip_count"],
            baseline_filters={"payment_type": 1},
            comparison_filters={"payment_type": 2},
            limit=10,
        )
        rows_by_key = {row[0]: row for row in result["rows"]}
        assert len(result["rows"]) == 2, (
            "expected exactly one merged row per rate_code key (including "
            f"NULL), got: {result['rows']}"
        )
        assert (
            None in rows_by_key
        ), f"NULL rate_code key must be present as a single merged row: {result['rows']}"
        null_row = rows_by_key[None]
        assert null_row[1] == 4  # baseline_trip_count
        assert null_row[2] == 2  # comparison_trip_count
        assert null_row[3] == -2  # delta_trip_count

        normal_row = rows_by_key[1]
        assert normal_row[1] == 5
        assert normal_row[2] == 3
        assert normal_row[3] == -2


class TestCompareTaxiSegmentsColumnBudget:
    """M3: the 16-column cap must be enforced for compare_taxi_segments too."""

    def test_too_many_measures_is_rejected_before_the_runner_is_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)

        def fail_if_called(*args: object, **kwargs: object) -> None:
            raise AssertionError("_run_governed_query must not be called")

        monkeypatch.setattr(analytics, "_run_governed_query", fail_if_called)

        # 1 dimension + 3 * 6 measures = 19 columns > MAX_COLUMNS (16)
        measures = [
            "trip_count",
            "average_fare",
            "median_fare",
            "average_tip",
            "median_tip",
            "total_tips",
        ]
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.compare_taxi_segments(
                parquet,
                zones,
                segment_dimension="pickup_zone",
                measures=measures,
                baseline_filters={},
                comparison_filters={},
            )
        assert excinfo.value.code == "invalid_column_budget"


class TestBucketDimensionsHandleNulls:
    """M4: NULL bucketed values must not silently land in the top bucket."""

    def test_null_passenger_count_is_bucketed_as_unknown(self, tmp_path: Path) -> None:
        from dataset_spike import analytics  # noqa: F401

        parquet = tmp_path / "yellow.parquet"
        zones = tmp_path / "zones.csv"
        connection = duckdb.connect()
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    (TIMESTAMP '2024-01-01 08:00:00', TIMESTAMP '2024-01-01 08:10:00',
                     1, 1, 1, NULL, 1.0, 0.0, 10.0, 10.0, 1, 1),
                    (TIMESTAMP '2024-01-01 09:00:00', TIMESTAMP '2024-01-01 09:10:00',
                     1, 1, 1, 8, 1.0, 0.0, 10.0, 10.0, 1, 1)
                ) AS trips(
                    tpep_pickup_datetime, tpep_dropoff_datetime,
                    PULocationID, DOLocationID, payment_type, passenger_count,
                    trip_distance, tip_amount, fare_amount, total_amount,
                    VendorID, RatecodeID
                )
            ) TO ? (FORMAT PARQUET)
            """,
            [str(parquet)],
        )
        connection.close()
        zones.write_text(
            "LocationID,Borough,Zone,service_zone\n1,Manhattan,Alpha,Boro Zone\n"
        )

        result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["passenger_count_bucket"],
            measures=["trip_count"],
        )
        buckets = {row[0]: row[1] for row in result["rows"]}
        assert buckets.get("unknown") == 1
        assert buckets.get("6+") == 1


class TestAirportTripIsAlwaysBoolean:
    """M5: airport_trip must resolve to true/false even with NULL inputs."""

    def test_null_ratecode_and_unmatched_zone_resolve_to_non_airport(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet = tmp_path / "yellow.parquet"
        zones = tmp_path / "zones.csv"
        connection = duckdb.connect()
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    -- PULocationID 99 has no matching zone row; RatecodeID NULL
                    (TIMESTAMP '2024-01-01 08:00:00', TIMESTAMP '2024-01-01 08:10:00',
                     99, 99, 1, 1.0, 1.0, 0.0, 10.0, 10.0, 1, NULL)
                ) AS trips(
                    tpep_pickup_datetime, tpep_dropoff_datetime,
                    PULocationID, DOLocationID, payment_type, passenger_count,
                    trip_distance, tip_amount, fare_amount, total_amount,
                    VendorID, RatecodeID
                )
            ) TO ? (FORMAT PARQUET)
            """,
            [str(parquet)],
        )
        connection.close()
        zones.write_text(
            "LocationID,Borough,Zone,service_zone\n1,Manhattan,Alpha,Boro Zone\n"
        )

        dimension_result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["airport_trip"],
            measures=["trip_count"],
        )
        assert dimension_result["rows"] == [["non_airport", 1]]

        filtered_result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["pickup_hour"],
            measures=["trip_count"],
            filters={"airport_trip": False},
        )
        assert (
            filtered_result["row_count"] == 1
        ), "airport_trip=false must not drop rows whose zone/RatecodeID are NULL"


class TestAirportTripDimensionIsSanitizerSafe:
    """M2: airport_trip must never surface a Python bool in a result row."""

    def test_aggregate_by_airport_trip_contains_no_bool_values(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.aggregate_taxi_data(
            parquet,
            zones,
            dimensions=["airport_trip"],
            measures=["trip_count"],
        )
        for row in result["rows"]:
            for value in row:
                assert not isinstance(value, bool), row
        labels = {row[0] for row in result["rows"]}
        assert labels <= {"airport", "non_airport"}

    def test_list_dimension_values_for_airport_trip_contains_no_bool_values(
        self, tmp_path: Path
    ) -> None:
        from dataset_spike import analytics

        parquet, zones = write_analytics_fixture(tmp_path)
        result = analytics.list_taxi_dimension_values(
            parquet, zones, dimension="airport_trip"
        )
        for row in result["rows"]:
            for value in row:
                assert not isinstance(value, bool), row
        labels = {row[0] for row in result["rows"]}
        assert labels <= {"airport", "non_airport"}

"""compare_taxi_segments comparison_mode=whole_segment (PR #193 finding 1)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import pytest
from dataset_spike import analytics
from dataset_spike.query_spec import QueryValidationError

AIRPORT_SQL = (
    "coalesce(pz.Zone IN ('JFK Airport','LaGuardia Airport','Newark Airport') "
    "OR dz.Zone IN ('JFK Airport','LaGuardia Airport','Newark Airport') "
    "OR t.RatecodeID IN (2, 3), false)"
)
VALID_SQL = (
    "t.tpep_pickup_datetime >= TIMESTAMP '2024-01-01' "
    "AND t.tpep_pickup_datetime < TIMESTAMP '2024-02-01' "
    "AND t.fare_amount >= 0 AND t.total_amount >= 0 "
    "AND t.tpep_dropoff_datetime >= t.tpep_pickup_datetime"
)


@pytest.fixture
def data(tmp_path: Path) -> tuple[Path, Path]:
    """Valid rows (M1-M3, Q1, Q2, A1, A2) plus three valid-record violators.

    Pickup Manhattan: M1 10, M2 20, M3 30, A1 80   -> count 4, avg fare 35.0
    Pickup Queens:    Q1 40, Q2 60, A2 100         -> count 3, avg fare 66.67
    Airport (A1, A2): fares 80,100 / minutes 60,40 -> 90.0 / 50.0
    Non-airport (5):  fares avg 32.0 / minutes avg 28.0
    The violators (negative fare, pickup outside Jan 2024, dropoff before pickup)
    are Manhattan pickups on airport rate codes, so they would distort both
    comparisons if the governed valid-record filter were not applied.
    """
    parquet = tmp_path / "yellow.parquet"
    zones = tmp_path / "zones.csv"
    rows = [
        # pickup, dropoff, PU, DO, pay, pax, dist, tip, fare, total, vendor, rate
        (
            "2024-01-01 08:00:00",
            "2024-01-01 08:10:00",
            1,
            1,
            1,
            1,
            1.0,
            2.0,
            10.0,
            12.0,
            1,
            1,
        ),
        (
            "2024-01-02 09:00:00",
            "2024-01-02 09:30:00",
            1,
            1,
            1,
            1,
            1.0,
            4.0,
            20.0,
            24.0,
            1,
            1,
        ),
        (
            "2024-01-03 10:00:00",
            "2024-01-03 10:20:00",
            1,
            2,
            2,
            1,
            1.0,
            0.0,
            30.0,
            30.0,
            1,
            1,
        ),
        (
            "2024-01-04 11:00:00",
            "2024-01-04 11:30:00",
            3,
            3,
            1,
            1,
            1.0,
            5.0,
            40.0,
            45.0,
            1,
            1,
        ),
        (
            "2024-01-05 12:00:00",
            "2024-01-05 12:50:00",
            3,
            1,
            2,
            1,
            1.0,
            0.0,
            60.0,
            60.0,
            1,
            1,
        ),
        (
            "2024-01-06 13:00:00",
            "2024-01-06 14:00:00",
            1,
            132,
            1,
            1,
            1.0,
            10.0,
            80.0,
            90.0,
            1,
            2,
        ),
        (
            "2024-01-07 14:00:00",
            "2024-01-07 14:40:00",
            132,
            3,
            2,
            1,
            1.0,
            0.0,
            100.0,
            110.0,
            1,
            2,
        ),
        # violators
        (
            "2024-01-08 08:00:00",
            "2024-01-08 08:10:00",
            1,
            1,
            1,
            1,
            1.0,
            0.0,
            -5.0,
            -5.0,
            1,
            2,
        ),
        (
            "2024-02-01 08:00:00",
            "2024-02-01 08:10:00",
            1,
            1,
            1,
            1,
            1.0,
            0.0,
            999.0,
            999.0,
            1,
            2,
        ),
        (
            "2024-01-09 08:10:00",
            "2024-01-09 08:00:00",
            1,
            1,
            1,
            1,
            1.0,
            0.0,
            888.0,
            888.0,
            1,
            2,
        ),
    ]
    values = ", ".join(
        f"(TIMESTAMP '{r[0]}', TIMESTAMP '{r[1]}', {', '.join(str(v) for v in r[2:])})"
        for r in rows
    )
    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT tpep_pickup_datetime, tpep_dropoff_datetime, PULocationID, "
        "DOLocationID, payment_type, passenger_count, trip_distance::DOUBLE AS "
        "trip_distance, tip_amount::DOUBLE AS tip_amount, fare_amount::DOUBLE AS "
        "fare_amount, total_amount::DOUBLE AS total_amount, VendorID, RatecodeID "
        f"FROM (VALUES {values}) AS trips("
        "tpep_pickup_datetime, tpep_dropoff_datetime, PULocationID, DOLocationID, "
        "payment_type, passenger_count, trip_distance, tip_amount, fare_amount, "
        "total_amount, VendorID, RatecodeID)) TO ? (FORMAT PARQUET)",
        [str(parquet)],
    )
    connection.close()
    zones.write_text(
        "LocationID,Borough,Zone,service_zone\n"
        "1,Manhattan,Alpha,Boro Zone\n"
        "2,Bronx,Beta,Boro Zone\n"
        "3,Queens,Gamma,Boro Zone\n"
        "132,Queens,JFK Airport,Airports\n"
    )
    return parquet, zones


def _sql(data: tuple[Path, Path], select: str, where: str) -> tuple[object, ...]:
    """Independent expected value straight from the test data."""
    parquet, zones = data
    connection = duckdb.connect()
    try:
        connection.execute(
            f"CREATE VIEW trips AS SELECT * FROM read_parquet('{parquet}')"
        )
        connection.execute(
            f"CREATE VIEW zones AS SELECT * FROM read_csv_auto('{zones}', header = true)"
        )
        return connection.execute(
            f"SELECT {select} FROM trips t "
            "LEFT JOIN zones pz ON t.PULocationID = pz.LocationID "
            "LEFT JOIN zones dz ON t.DOLocationID = dz.LocationID "
            f"WHERE {VALID_SQL} AND {where}"
        ).fetchone()  # type: ignore[return-value]
    finally:
        connection.close()


def _by_measure(result: dict[str, object]) -> dict[str, list[object]]:
    assert result["columns"] == [
        "measure",
        "baseline",
        "comparison",
        "delta",
        "delta_pct",
    ]
    return {row[0]: row for row in result["rows"]}  # type: ignore[index, union-attr]


def _whole(data: tuple[Path, Path], **kwargs: object) -> dict[str, object]:
    parquet, zones = data
    return analytics.compare_taxi_segments(
        parquet, zones, comparison_mode="whole_segment", **kwargs
    )


def test_airport_vs_non_airport_whole_segment_values_and_deltas(data) -> None:
    result = _whole(
        data,
        measures=["average_fare", "average_duration_minutes", "trip_count"],
        baseline_filters={"airport_trip": False},
        comparison_filters={"airport_trip": True},
    )
    rows = _by_measure(result)
    expected_fare_airport = _sql(data, "round(avg(t.fare_amount), 2)", AIRPORT_SQL)[0]
    expected_fare_other = _sql(
        data, "round(avg(t.fare_amount), 2)", f"NOT {AIRPORT_SQL}"
    )[0]
    assert (expected_fare_other, expected_fare_airport) == (32.0, 90.0)
    assert rows["average_fare"][1:] == [32.0, 90.0, 58.0, 181.25]
    assert rows["average_duration_minutes"][1:] == [28.0, 50.0, 22.0, 78.5714]
    assert rows["trip_count"][1:] == [5, 2, -3, -60.0]
    assert result["comparison_mode"] == "whole_segment"
    assert result["baseline_label"] == "airport_trip=false"
    assert result["comparison_label"] == "airport_trip=true"
    assert result["query_class"] == "compare_segments"
    assert "no_data_sides" not in result
    assert "segment_dimension" not in result
    assert str(result["query_id"]).startswith("query_")
    assert result["truncated"] is False


def test_manhattan_vs_queens_whole_segment_values_and_deltas(data) -> None:
    result = _whole(
        data,
        measures=["trip_count", "average_fare", "average_tip"],
        baseline_filters={"pickup_borough": "Manhattan"},
        comparison_filters={"pickup_borough": "Queens"},
    )
    rows = _by_measure(result)
    sql_m = _sql(
        data,
        "count(*), round(avg(t.fare_amount), 2)",
        "lower(pz.Borough) = 'manhattan'",
    )
    sql_q = _sql(
        data, "count(*), round(avg(t.fare_amount), 2)", "lower(pz.Borough) = 'queens'"
    )
    assert (sql_m, sql_q) == ((4, 35.0), (3, 66.67))
    assert rows["trip_count"][1:] == [4, 3, -1, -25.0]
    assert rows["average_fare"][1:4] == [35.0, 66.67, 31.67]
    assert rows["average_fare"][4] == pytest.approx(90.4857, abs=1e-4)
    # card-only tips: Manhattan (2+4+10)/3, Queens 5/1
    assert rows["average_tip"][1:3] == [5.33, 5.0]
    assert rows["average_tip"][3] == -0.33
    assert result["baseline_label"] == "pickup_borough=Manhattan"
    assert result["comparison_label"] == "pickup_borough=Queens"


def test_empty_side_returns_explicit_nulls_not_zeros(data) -> None:
    result = _whole(
        data,
        measures=["trip_count", "average_fare"],
        baseline_filters={"pickup_borough": "Manhattan"},
        comparison_filters={"pickup_borough": "Staten Island"},
    )
    rows = _by_measure(result)
    assert rows["trip_count"][1:] == [4, None, None, None]
    assert rows["average_fare"][1:] == [35.0, None, None, None]
    assert result["no_data_sides"] == ["comparison"]

    empty_baseline = _whole(
        data,
        measures=["trip_count"],
        baseline_filters={"pickup_borough": "Staten Island"},
        comparison_filters={"pickup_borough": "Queens"},
    )
    assert _by_measure(empty_baseline)["trip_count"][1:] == [None, 3, None, None]
    assert empty_baseline["no_data_sides"] == ["baseline"]

    both = _whole(
        data,
        measures=["trip_count"],
        baseline_filters={"pickup_borough": "Staten Island"},
        comparison_filters={"pickup_borough": "EWR"},
    )
    assert both["no_data_sides"] == ["baseline", "comparison"]


def test_zero_baseline_gives_null_percentage_never_infinity(data) -> None:
    # Cash trips have recorded tip 0 -> total_tips baseline is a real 0.
    result = _whole(
        data,
        measures=["total_tips", "trip_count"],
        baseline_filters={"payment_type": 2},
        comparison_filters={"payment_type": 1},
    )
    row = _by_measure(result)["total_tips"]
    assert row[1] == 0.0
    assert row[2] == 21.0
    assert row[3] == 21.0
    assert row[4] is None
    json.dumps(result, allow_nan=False)  # no inf/NaN anywhere
    for value in (v for r in result["rows"] for v in r[1:]):  # type: ignore[union-attr]
        assert value is None or math.isfinite(value)


def test_invalid_mode_and_combinations_rejected_before_execution(
    data, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("_run_governed_query must not be called")

    monkeypatch.setattr(analytics, "_run_governed_query", fail)
    parquet, zones = data
    common = {
        "measures": ["trip_count"],
        "baseline_filters": {},
        "comparison_filters": {},
    }
    cases = [
        (
            {"comparison_mode": "bogus", "segment_dimension": "pickup_hour"},
            "invalid_comparison_mode",
        ),
        ({"comparison_mode": None}, "invalid_comparison_mode"),
        (
            {"comparison_mode": "whole_segment", "segment_dimension": "pickup_hour"},
            "invalid_comparison_combination",
        ),
        ({"comparison_mode": "by_dimension"}, "invalid_comparison_combination"),
        ({}, "invalid_comparison_combination"),
    ]
    for extra, code in cases:
        with pytest.raises(QueryValidationError) as excinfo:
            analytics.compare_taxi_segments(parquet, zones, **common, **extra)
        assert excinfo.value.code == code, extra
    with pytest.raises(QueryValidationError) as excinfo:
        analytics.compare_taxi_segments(
            parquet,
            zones,
            comparison_mode="whole_segment",
            measures=["trip_share"],
            baseline_filters={},
            comparison_filters={},
        )
    assert excinfo.value.code == "invalid_measures"


def test_whole_segment_applies_the_governed_valid_record_filter(data) -> None:
    parquet, zones = data
    manhattan = {"pickup_borough": "Manhattan"}
    queens = {"pickup_borough": "Queens"}
    governed = _whole(
        data,
        measures=["trip_count", "average_fare"],
        baseline_filters=manhattan,
        comparison_filters=queens,
    )
    rows = _by_measure(governed)
    # Violators (fare -5, Feb 1 pickup, dropoff before pickup) are excluded.
    assert rows["trip_count"][1] == 4
    assert rows["average_fare"][1] == 35.0

    # Identical to the by_dimension path summed over its segments.
    by_dim = analytics.compare_taxi_segments(
        parquet,
        zones,
        segment_dimension="pickup_hour",
        measures=["trip_count"],
        baseline_filters=manhattan,
        comparison_filters=queens,
    )
    assert sum(r[1] or 0 for r in by_dim["rows"]) == rows["trip_count"][1]
    assert sum(r[2] or 0 for r in by_dim["rows"]) == rows["trip_count"][2]

    # Opting out of the governed filter (a validated filter) includes them,
    # proving the default really is filtering.
    ungoverned = _whole(
        data,
        measures=["trip_count"],
        baseline_filters={**manhattan, "valid_records_only": False},
        comparison_filters=queens,
    )
    assert _by_measure(ungoverned)["trip_count"][1] == 7
    assert (
        ungoverned["baseline_label"]
        == "pickup_borough=Manhattan, valid_records_only=false"
    )


def test_by_dimension_default_and_explicit_mode_are_identical(data) -> None:
    parquet, zones = data
    kwargs = {
        "segment_dimension": "pickup_hour",
        "measures": ["trip_count"],
        "baseline_filters": {"pickup_week_part": "weekday"},
        "comparison_filters": {"pickup_week_part": "weekend"},
        "query_id_factory": lambda: "query_fixed",
    }
    default = analytics.compare_taxi_segments(parquet, zones, **kwargs)
    explicit = analytics.compare_taxi_segments(
        parquet, zones, comparison_mode="by_dimension", **kwargs
    )
    default.pop("execution_duration_ms")
    explicit.pop("execution_duration_ms")
    assert default == explicit
    assert "comparison_mode" not in default
    assert default["columns"][0] == "pickup_hour"


def test_whole_segment_respects_timeout(data, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataset_spike.query_compiler as compiler

    def slow(**kwargs: object):
        return compiler.CompiledQuery(
            sql="SELECT sum(i) AS a, sum(i) AS b FROM range(1000000000000) AS v(i)",
            parameters=[],
            output_columns=["a", "b"],
        )

    monkeypatch.setattr(analytics, "compile_compare_whole_segments_query", slow)
    with pytest.raises(TimeoutError):
        _whole(
            data,
            measures=["trip_count"],
            baseline_filters={},
            comparison_filters={},
            timeout_seconds=0.01,
        )

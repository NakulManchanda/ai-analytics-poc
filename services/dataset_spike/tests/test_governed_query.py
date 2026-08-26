from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest


def write_query_fixture(tmp_path: Path) -> tuple[Path, Path]:
    parquet = tmp_path / "yellow.parquet"
    zones = tmp_path / "zones.csv"
    connection = duckdb.connect()
    connection.execute(
        """
        COPY (
            SELECT * FROM (VALUES
                (TIMESTAMP '2024-01-01 08:00:00', 1, 2.0, 10.0),
                (TIMESTAMP '2024-01-01 08:30:00', 1, 4.0, 14.0),
                (TIMESTAMP '2024-01-02 17:00:00', 2, 8.0, 20.0),
                (TIMESTAMP '2024-01-03 17:30:00', 2, 6.0, 16.0),
                (TIMESTAMP '2024-01-03 17:45:00', 1, 10.0, 30.0)
            ) AS trips(tpep_pickup_datetime, PULocationID, trip_distance, fare_amount)
        ) TO ? (FORMAT PARQUET)
        """,
        [str(parquet)],
    )
    connection.close()
    zones.write_text(
        "LocationID,Borough,Zone,service_zone\n"
        "1,Manhattan,Alpha,Boro Zone\n"
        "2,Bronx,Beta,Boro Zone\n"
    )
    return parquet, zones


def test_average_trip_metrics_compares_governed_pickup_regions(
    tmp_path: Path,
) -> None:
    """Catches grouping by zones or returning the wrong regional metric values."""
    from dataset_spike.analytics import query_average_trip_metrics

    parquet, zones = write_query_fixture(tmp_path)

    result = query_average_trip_metrics(
        parquet,
        zones,
        query_id_factory=lambda: "query_region_fixture",
    )

    assert result == {
        "columns": [
            "region_name",
            "trip_count",
            "average_trip_distance",
            "average_fare_amount",
        ],
        "rows": [
            ["Manhattan", 3, 5.33, 18.0],
            ["Bronx", 2, 7.0, 18.0],
        ],
        "row_count": 2,
        "execution_duration_ms": pytest.approx(0, abs=5_000),
        "query_id": "query_region_fixture",
        "truncated": False,
    }


def test_average_trip_metrics_filters_a_case_insensitive_region_name(
    tmp_path: Path,
) -> None:
    """Catches passing caller text into grouping or returning every region."""
    from dataset_spike.analytics import query_average_trip_metrics

    parquet, zones = write_query_fixture(tmp_path)

    result = query_average_trip_metrics(
        parquet,
        zones,
        region_name="  manHATTan  ",
        query_id_factory=lambda: "query_one_region",
    )

    assert result["rows"] == [["Manhattan", 3, 5.33, 18.0]]
    assert result["row_count"] == 1
    assert result["truncated"] is False


@pytest.mark.parametrize(
    "region_name", ["", "   ", "Unknown", "N/A", "EWR", "Atlantis"]
)
def test_average_trip_metrics_rejects_invalid_or_non_borough_region_names(
    tmp_path: Path, region_name: str
) -> None:
    """Catches accepting sentinel or invented regions as governed inputs."""
    from dataset_spike.analytics import (
        RegionValidationError,
        query_average_trip_metrics,
    )

    parquet, zones = write_query_fixture(tmp_path)

    with pytest.raises(
        RegionValidationError,
        match="region_name must be a recognized pickup borough",
    ):
        query_average_trip_metrics(parquet, zones, region_name=region_name)


@pytest.mark.parametrize(
    ("analysis", "expected_columns", "expected_first_row"),
    [
        ("top_pickup_zones", ["pickup_zone", "trip_count"], ["Alpha", 3]),
        ("trip_volume_by_hour", ["pickup_hour", "trip_count"], [17, 3]),
        (
            "average_distance_by_weekday",
            ["pickup_weekday", "average_trip_distance"],
            ["Monday", 3.0],
        ),
    ],
)
def test_governed_query_executes_only_allowlisted_analysis_shapes(
    tmp_path: Path,
    analysis: str,
    expected_columns: list[str],
    expected_first_row: list[object],
) -> None:
    """Catches a wrong analysis-to-query mapping or an unbounded result shape."""
    from dataset_spike.analytics import query_dataset

    parquet, zones = write_query_fixture(tmp_path)

    result = query_dataset(
        parquet,
        zones,
        analysis=analysis,
        limit=1,
        query_id_factory=lambda: "query_fixture",
    )

    assert result == {
        "columns": expected_columns,
        "rows": [expected_first_row],
        "row_count": 1,
        "execution_duration_ms": pytest.approx(0, abs=5_000),
        "query_id": "query_fixture",
        "truncated": True,
    }


@pytest.mark.parametrize(
    ("analysis", "limit"),
    [("read_parquet", 5), ("top_pickup_zones", 0), ("top_pickup_zones", 21)],
)
def test_governed_query_rejects_unknown_analysis_and_out_of_range_limits(
    tmp_path: Path, analysis: str, limit: int
) -> None:
    """Catches accepting a caller-controlled function name or unbounded limit."""
    from dataset_spike.analytics import query_dataset

    parquet, zones = write_query_fixture(tmp_path)

    with pytest.raises(ValueError):
        query_dataset(parquet, zones, analysis=analysis, limit=limit)


def test_governed_query_truncates_rows_to_the_serialized_byte_limit(
    tmp_path: Path,
) -> None:
    """Catches returning a valid row set whose serialized envelope exceeds the cap."""
    from dataset_spike.analytics import query_dataset

    parquet, zones = write_query_fixture(tmp_path)

    result = query_dataset(
        parquet,
        zones,
        analysis="top_pickup_zones",
        limit=2,
        max_result_bytes=150,
        query_id_factory=lambda: "query_bytes",
    )

    assert len(json.dumps(result, separators=(",", ":")).encode()) <= 150
    assert result["truncated"] is True
    assert result["row_count"] < 2


def test_governed_query_interrupts_execution_after_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a DuckDB query continuing beyond its configured execution deadline."""
    import dataset_spike.analytics as analytics

    parquet, zones = write_query_fixture(tmp_path)
    monkeypatch.setitem(
        analytics.ALLOWED_ANALYSES,
        "top_pickup_zones",
        "SELECT sum(i) AS total FROM range(1000000000000) AS values(i) LIMIT ?",
    )

    with pytest.raises(TimeoutError):
        analytics.query_dataset(
            parquet,
            zones,
            analysis="top_pickup_zones",
            timeout_seconds=0.01,
        )

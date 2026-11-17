from __future__ import annotations

from pathlib import Path

import pytest
from dataset_spike import analytics
from dataset_spike.query_compiler import compile_zone_measure_query
from dataset_spike.query_spec import (
    MAX_ZONE_ROWS,
    MeasureName,
    QueryValidationError,
    TaxiFilters,
)
from test_governed_analytics import write_analytics_fixture


def test_compiled_sql_binds_values_and_never_interpolates_them() -> None:
    compiled = compile_zone_measure_query(
        "pickup",
        MeasureName.TRIP_COUNT,
        TaxiFilters(pickup_borough="Queens'; DROP TABLE trips; --", pickup_hour_min=7),
    )
    assert "DROP TABLE" not in compiled.sql
    assert "GROUP BY t.PULocationID, pz.Zone, pz.Borough" in compiled.sql
    assert "Queens'; DROP TABLE trips; --" in compiled.parameters
    assert compiled.parameters[-1] == MAX_ZONE_ROWS + 1
    assert compiled.output_columns == ["location_id", "zone", "borough", "trip_count"]


def test_dropoff_role_groups_by_dropoff_location() -> None:
    compiled = compile_zone_measure_query("dropoff", MeasureName.AVERAGE_FARE, None)
    assert "GROUP BY t.DOLocationID, dz.Zone, dz.Borough" in compiled.sql


def test_returns_one_row_per_zone_id(tmp_path: Path) -> None:
    parquet, zones = write_analytics_fixture(tmp_path)
    result = analytics.map_taxi_zones(
        parquet, zones, zone_role="pickup", measure="trip_count"
    )
    assert result["columns"] == ["location_id", "zone", "borough", "trip_count"]
    # Valid records only: the negative-fare pickup at zone 1 is excluded.
    assert result["rows"] == [[1, "Alpha", "Manhattan", 2], [2, "Beta", "Bronx", 2]]
    assert result["row_count"] == 2
    assert result["truncated"] is False
    assert result["query_class"] == "zone_map"
    assert str(result["query_id"]).startswith("query_")


def test_filters_are_applied(tmp_path: Path) -> None:
    parquet, zones = write_analytics_fixture(tmp_path)
    result = analytics.map_taxi_zones(
        parquet,
        zones,
        zone_role="dropoff",
        measure="trip_count",
        filters={"pickup_week_part": "weekend"},
    )
    assert result["rows"] == [[1, "Alpha", "Manhattan", 1], [2, "Beta", "Bronx", 1]]


def test_row_cap_is_265(tmp_path: Path) -> None:
    assert MAX_ZONE_ROWS == 265


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"zone_role": "sideways", "measure": "trip_count"}, "invalid_zone_role"),
        ({"zone_role": "pickup", "measure": "nope"}, "unknown_measure"),
        (
            {"zone_role": "pickup", "measure": "trip_count", "filters": {"bad": 1}},
            "unknown_filter",
        ),
    ],
)
def test_validation_errors_do_not_reach_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict, code: str
) -> None:
    parquet, zones = write_analytics_fixture(tmp_path)
    monkeypatch.setattr(
        analytics,
        "_run_governed_query",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    with pytest.raises(QueryValidationError) as excinfo:
        analytics.map_taxi_zones(parquet, zones, **kwargs)
    assert excinfo.value.code == code

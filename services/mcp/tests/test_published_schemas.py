import asyncio

from dataset_spike.analytics import COMPARISON_MODES, DatasetProfile
from dataset_spike.query_spec import (
    WEEKDAY_NAMES,
    DimensionName,
    MeasureName,
    build_aggregate_spec,
)
from fastmcp import Client
from mcp_server.server import build_mcp


def _profile() -> DatasetProfile:
    return DatasetProfile(1, 1, [], [], {}, 1, 1)


def _schemas() -> dict[str, dict]:
    async def exercise():
        async with Client(build_mcp(profile_loader=_profile)) as client:
            return await client.list_tools()

    return {t.name: t.inputSchema for t in asyncio.run(exercise())}


def _non_null(schema: dict) -> dict:
    options = schema.get("anyOf")
    if not options:
        return schema
    return next(o for o in options if o.get("type") != "null")


def _assert_typed_filters(filters: dict) -> None:
    props = _non_null(filters)["properties"]
    assert set(
        props["pickup_weekday"].get("anyOf", [props["pickup_weekday"]])[0]["enum"]
    ) == (WEEKDAY_NAMES)
    hour = props["pickup_hour_min"]["anyOf"][0]
    assert (hour["minimum"], hour["maximum"]) == (0, 23)
    assert "credit_card" in props["payment_type"]["description"]
    assert props["valid_records_only"]["type"] == "boolean"


def test_aggregate_schema_is_specific():
    schema = _schemas()["aggregate_taxi_data"]
    props = schema["properties"]
    assert props["dimensions"]["items"]["enum"] == [d.value for d in DimensionName]
    assert props["measures"]["items"]["enum"] == [m.value for m in MeasureName]
    _assert_typed_filters(props["filters"])
    order_by = _non_null(props["order_by"])["properties"]
    assert order_by["direction"]["enum"] == ["asc", "desc"]
    assert set(schema["required"]) == {"dimensions", "measures"}


def test_compare_schema_is_specific():
    schema = _schemas()["compare_taxi_segments"]
    props = schema["properties"]
    _assert_typed_filters(props["baseline_filters"])
    _assert_typed_filters(props["comparison_filters"])
    assert _non_null(props["segment_dimension"])["enum"] == [
        d.value for d in DimensionName
    ]
    assert _non_null(props["comparison_mode"])["enum"] == list(COMPARISON_MODES)
    assert set(schema["required"]) == {
        "measures",
        "baseline_filters",
        "comparison_filters",
    }


def test_list_dimension_values_schema_has_dimension_enum():
    schema = _schemas()["list_taxi_dimension_values"]
    assert schema["properties"]["dimension"]["enum"] == [d.value for d in DimensionName]


def test_unknown_filter_still_returns_validator_envelope():
    """The richer published schema must not change runtime validation."""

    def validating_runner(*, dimensions, measures, filters, order_by, limit):
        build_aggregate_spec(
            dimensions=dimensions,
            measures=measures,
            filters=filters,
            order_by=order_by,
            limit=limit,
        )
        raise AssertionError("unreachable")

    async def exercise():
        async with Client(
            build_mcp(profile_loader=_profile, aggregate_runner=validating_runner)
        ) as client:
            return await client.call_tool(
                "aggregate_taxi_data",
                {
                    "dimensions": ["pickup_borough"],
                    "measures": ["trip_count"],
                    "filters": {"bogus": 1},
                },
            )

    error = asyncio.run(exercise()).data["error"]
    assert error["code"] == "unknown_filter"
    assert error["retryable"] is False

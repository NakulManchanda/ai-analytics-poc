import asyncio

from dataset_spike.analytics import DatasetProfile
from dataset_spike.query_spec import QueryValidationError
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _profile() -> DatasetProfile:
    return DatasetProfile(1, 1, [], [], {}, 1, 1)


def test_new_tools_are_exposed_and_call_their_injected_runners():
    from fastmcp import Client
    from mcp_server.server import build_mcp

    describe_calls = []
    dimension_calls = []
    aggregate_calls = []
    compare_calls = []

    def describe_runner(**kwargs):
        describe_calls.append(kwargs)
        return {
            "columns": [{"name": "pickup_zone", "type": "VARCHAR"}],
            "row_count": 5,
            "truncated": False,
            "query_class": "describe",
            "query_id": "q1",
        }

    def dimension_values_runner(**kwargs):
        dimension_calls.append(kwargs)
        return {
            "columns": ["value", "trip_count"],
            "rows": [["Manhattan", 3]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "q2",
            "truncated": False,
        }

    def aggregate_runner(**kwargs):
        aggregate_calls.append(kwargs)
        return {
            "columns": ["pickup_hour", "trip_count"],
            "rows": [[8, 2]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "q3",
            "truncated": False,
            "query_class": "aggregate",
            "dimensions": ["pickup_hour"],
            "measures": ["trip_count"],
        }

    def compare_runner(**kwargs):
        compare_calls.append(kwargs)
        return {
            "columns": [
                "pickup_borough",
                "baseline_trip_count",
                "comparison_trip_count",
                "delta_trip_count",
            ],
            "rows": [["Manhattan", 3, 1, -2]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "q4",
            "truncated": False,
            "query_class": "compare_segments",
        }

    async def exercise():
        async with Client(
            build_mcp(
                profile_loader=_profile,
                describe_runner=describe_runner,
                dimension_values_runner=dimension_values_runner,
                aggregate_runner=aggregate_runner,
                compare_segments_runner=compare_runner,
            )
        ) as client:
            describe = await client.call_tool("describe_taxi_dataset", {})
            values = await client.call_tool(
                "list_taxi_dimension_values", {"dimension": "pickup_borough"}
            )
            aggregate = await client.call_tool(
                "aggregate_taxi_data",
                {"dimensions": ["pickup_hour"], "measures": ["trip_count"]},
            )
            compare = await client.call_tool(
                "compare_taxi_segments",
                {
                    "segment_dimension": "pickup_borough",
                    "measures": ["trip_count"],
                    "baseline_filters": {"pickup_week_part": "weekday"},
                    "comparison_filters": {"pickup_week_part": "weekend"},
                },
            )
        return describe, values, aggregate, compare

    describe, values, aggregate, compare = asyncio.run(exercise())

    assert describe.data["row_count"] == 5
    assert values.data["rows"] == [["Manhattan", 3]]
    assert aggregate.data["rows"] == [[8, 2]]
    assert compare.data["rows"] == [["Manhattan", 3, 1, -2]]
    assert describe_calls == [{"include_column_stats": False}]
    assert dimension_calls == [
        {"dimension": "pickup_borough", "search": None, "limit": 20}
    ]
    assert aggregate_calls == [
        {
            "dimensions": ["pickup_hour"],
            "measures": ["trip_count"],
            "filters": None,
            "order_by": None,
            "limit": 20,
        }
    ]
    assert compare_calls == [
        {
            "segment_dimension": "pickup_borough",
            "measures": ["trip_count"],
            "baseline_filters": {"pickup_week_part": "weekday"},
            "comparison_filters": {"pickup_week_part": "weekend"},
            "limit": 20,
        }
    ]


def test_validation_error_returns_structured_non_retryable_envelope():
    from fastmcp import Client
    from mcp_server.server import build_mcp

    def failing_aggregate_runner(**kwargs):
        raise QueryValidationError("unknown_dimension", "'bogus' is not allowlisted")

    async def exercise():
        async with Client(
            build_mcp(
                profile_loader=_profile, aggregate_runner=failing_aggregate_runner
            )
        ) as client:
            return await client.call_tool(
                "aggregate_taxi_data",
                {"dimensions": ["bogus"], "measures": ["trip_count"]},
            )

    result = asyncio.run(exercise())
    assert result.data == {
        "error": {
            "code": "unknown_dimension",
            "message": "'bogus' is not allowlisted",
            "retryable": False,
        }
    }


def test_validation_failure_never_reaches_duckdb():
    """A malformed request must fail via the spec validator, never inside the runner."""
    from dataset_spike.query_spec import build_aggregate_spec
    from fastmcp import Client
    from mcp_server.server import build_mcp

    executed = False

    def validating_runner(*, dimensions, measures, filters, order_by, limit):
        nonlocal executed
        # Mirrors the real analytics function: validate before ever touching DuckDB.
        build_aggregate_spec(
            dimensions=dimensions,
            measures=measures,
            filters=filters,
            order_by=order_by,
            limit=limit,
        )
        executed = True
        raise AssertionError("must not reach DuckDB execution for invalid input")

    async def exercise():
        async with Client(
            build_mcp(profile_loader=_profile, aggregate_runner=validating_runner)
        ) as client:
            return await client.call_tool(
                "aggregate_taxi_data",
                {"dimensions": [], "measures": ["trip_count"]},
            )

    result = asyncio.run(exercise())
    assert result.data["error"]["code"] == "invalid_dimensions"
    assert result.data["error"]["retryable"] is False
    assert executed is False


def test_governed_tool_call_creates_bounded_telemetry_attributes():
    from fastmcp import Client
    from mcp_server.server import build_mcp

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    def aggregate_runner(**kwargs):
        return {
            "columns": ["pickup_hour", "trip_count"],
            "rows": [[8, 2]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "q5",
            "truncated": False,
            "query_class": "aggregate",
            "dimensions": ["pickup_hour"],
            "measures": ["trip_count"],
        }

    async def exercise():
        async with Client(
            build_mcp(
                profile_loader=_profile,
                aggregate_runner=aggregate_runner,
                tracer=tracer,
            )
        ) as client:
            return await client.call_tool(
                "aggregate_taxi_data",
                {"dimensions": ["pickup_hour"], "measures": ["trip_count"]},
            )

    try:
        asyncio.run(exercise())
    finally:
        provider.shutdown()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    duckdb_span = spans["duckdb.query"]
    assert duckdb_span.attributes["ai.tool.name"] == "aggregate_taxi_data"
    assert duckdb_span.attributes["ai.query_class"] == "aggregate"
    assert duckdb_span.attributes["ai.dimensions"] == "pickup_hour"
    assert duckdb_span.attributes["ai.measures"] == "trip_count"
    assert duckdb_span.attributes["ai.row_count"] == 1
    assert duckdb_span.attributes["ai.truncated"] is False
    assert duckdb_span.attributes["ai.validation_result"] == "valid"
    # No raw SQL, filter values, or prompt text ever appear on the span.
    import json

    exported = json.dumps(dict(duckdb_span.attributes))
    assert "SELECT" not in exported

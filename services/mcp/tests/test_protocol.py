import asyncio
import json
from dataclasses import asdict

import pytest
from dataset_spike.analytics import DatasetProfile, RegionValidationError
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def assert_span_excludes(span, sensitive_value: str) -> None:
    """Check every exported span field that can carry application data."""
    exported_data = {
        "attributes": dict(span.attributes),
        "events": [
            {"name": event.name, "attributes": dict(event.attributes)}
            for event in span.events
        ],
        "resource_attributes": dict(span.resource.attributes),
        "status_description": span.status.description,
    }
    assert sensitive_value not in json.dumps(exported_data, default=str)


def test_dataset_contract_exposes_fixed_schema_and_profile_over_mcp():
    """Fails if the MCP surface stops exposing the bounded dataset contract."""
    from fastmcp import Client
    from mcp_server.server import build_mcp

    expected_profile = DatasetProfile(
        row_count=3,
        zone_row_count=2,
        schema_columns=["tpep_pickup_datetime", "PULocationID", "total_amount"],
        daily_zone_rows=[
            {
                "pickup_date": "2024-01-01",
                "pickup_zone": "Alpha",
                "trip_count": 2,
                "total_amount": 22.5,
            }
        ],
        duckdb_settings={"threads": "1", "memory_limit": "512MB"},
        timing_ms=1,
        rss_bytes=1024,
    )
    loader_call_count = 0

    def profile_loader() -> DatasetProfile:
        nonlocal loader_call_count
        loader_call_count += 1
        return expected_profile

    query_requests: list[tuple[str, int]] = []

    def query_runner(*, analysis: str, limit: int) -> dict[str, object]:
        query_requests.append((analysis, limit))
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["Alpha", 2]],
            "row_count": 1,
            "execution_duration_ms": 4,
            "query_id": "query_protocol",
            "truncated": False,
        }

    average_requests: list[str | None] = []

    def average_metrics_runner(*, region_name: str | None = None) -> dict[str, object]:
        average_requests.append(region_name)
        if region_name == "Atlantis":
            raise RegionValidationError(
                "region_name must be a recognized pickup borough"
            )
        return {
            "columns": [
                "region_name",
                "trip_count",
                "average_trip_distance",
                "average_fare_amount",
            ],
            "rows": [[region_name or "Manhattan", 3, 5.33, 18.0]],
            "row_count": 1,
            "execution_duration_ms": 4,
            "query_id": "query_average_protocol",
            "truncated": False,
        }

    async def exercise_protocol():
        async with Client(
            build_mcp(
                profile_loader=profile_loader,
                query_runner=query_runner,
                average_metrics_runner=average_metrics_runner,
            )
        ) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            schema = await client.read_resource("dataset://nyc-taxi/schema")
            profile = await client.call_tool("get_dataset_profile")
            query = await client.call_tool(
                "query_taxi_data",
                {"analysis": "top_pickup_zones", "limit": 2},
            )
            averages = await client.call_tool("average_trip_metrics")
            filtered_averages = await client.call_tool(
                "average_trip_metrics", {"region_name": "Bronx"}
            )
            invalid_region = await client.call_tool(
                "average_trip_metrics", {"region_name": "Atlantis"}
            )
        return (
            tools,
            resources,
            schema,
            profile,
            query,
            averages,
            filtered_averages,
            invalid_region,
        )

    (
        tools,
        resources,
        schema,
        profile,
        query,
        averages,
        filtered_averages,
        invalid_region,
    ) = asyncio.run(exercise_protocol())

    assert [tool.name for tool in tools] == [
        "get_dataset_profile",
        "query_taxi_data",
        "average_trip_metrics",
    ]
    average_tool = next(tool for tool in tools if tool.name == "average_trip_metrics")
    assert average_tool.inputSchema["type"] == "object"
    assert average_tool.inputSchema["properties"]["region_name"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
    }
    assert [str(resource.uri) for resource in resources] == [
        "dataset://nyc-taxi/schema"
    ]
    assert schema[0].text == (
        '{"columns":["tpep_pickup_datetime","PULocationID","total_amount"],'
        '"dataset":"nyc-yellow-taxi","month":"2024-01"}'
    )
    assert profile.data == asdict(expected_profile)
    assert query.data == {
        "columns": ["pickup_zone", "trip_count"],
        "rows": [["Alpha", 2]],
        "row_count": 1,
        "execution_duration_ms": 4,
        "query_id": "query_protocol",
        "truncated": False,
    }
    assert query_requests == [("top_pickup_zones", 2)]
    assert averages.data["rows"] == [["Manhattan", 3, 5.33, 18.0]]
    assert filtered_averages.data["rows"] == [["Bronx", 3, 5.33, 18.0]]
    assert invalid_region.data == {
        "error": {
            "code": "invalid_region_name",
            "message": "region_name must be a recognized pickup borough",
            "retryable": False,
        }
    }
    assert average_requests == [None, "Bronx", "Atlantis"]
    assert loader_call_count == 1


def test_governed_query_creates_safe_tool_and_duckdb_spans():
    """A governed query must trace only its fixed metadata, never its result."""
    from fastmcp import Client
    from mcp_server.server import build_mcp

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    profile = DatasetProfile(
        row_count=1,
        zone_row_count=1,
        schema_columns=["pickup_zone"],
        daily_zone_rows=[],
        duckdb_settings={},
        timing_ms=1,
        rss_bytes=1,
    )

    def query_runner(*, analysis: str, limit: int) -> dict[str, object]:
        assert (analysis, limit) == ("top_pickup_zones", 2)
        return {
            "columns": ["pickup_zone"],
            "rows": [["Alpha"]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "query_trace",
            "truncated": False,
        }

    async def exercise_query():
        async with Client(
            build_mcp(
                profile_loader=lambda: profile,
                query_runner=query_runner,
                tracer=tracer,
            )
        ) as client:
            return await client.call_tool(
                "query_taxi_data",
                {"analysis": "top_pickup_zones", "limit": 2},
            )

    try:
        result = asyncio.run(exercise_query())
    finally:
        provider.shutdown()

    assert result.data["rows"] == [["Alpha"]]
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["mcp.tool.execute"].attributes == {"mcp.tool.name": "query_taxi_data"}
    assert spans["duckdb.query"].attributes == {
        "db.system.name": "duckdb",
        "ai.tool.name": "query_taxi_data",
        "ai.analysis": "top_pickup_zones",
        "ai.row_limit": 2,
    }
    assert (
        spans["duckdb.query"].parent.span_id
        == spans["mcp.tool.execute"].context.span_id
    )
    for span in spans.values():
        assert_span_excludes(span, "Alpha")


def test_reading_schema_creates_safe_fixed_resource_span():
    """Schema reads must identify only the fixed resource URI, not its body."""
    from fastmcp import Client
    from mcp_server.server import build_mcp

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    profile = DatasetProfile(
        row_count=1,
        zone_row_count=1,
        schema_columns=["Alpha"],
        daily_zone_rows=[],
        duckdb_settings={},
        timing_ms=1,
        rss_bytes=1,
    )

    async def exercise_resource():
        async with Client(
            build_mcp(profile_loader=lambda: profile, tracer=tracer)
        ) as client:
            return await client.read_resource("dataset://nyc-taxi/schema")

    try:
        result = asyncio.run(exercise_resource())
    finally:
        provider.shutdown()

    assert (
        result[0].text
        == '{"columns":["Alpha"],"dataset":"nyc-yellow-taxi","month":"2024-01"}'
    )
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["mcp.resource.read"].attributes == {
        "mcp.resource.uri": "dataset://nyc-taxi/schema"
    }
    assert_span_excludes(spans["mcp.resource.read"], "Alpha")


def test_failing_query_marks_tool_and_duckdb_spans_as_errors_without_arguments():
    """Runner failures retain FastMCP failures while trace metadata stays private."""
    from fastmcp import Client
    from fastmcp.exceptions import ToolError
    from mcp_server.server import build_mcp
    from opentelemetry.trace import StatusCode

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    profile = DatasetProfile(1, 1, [], [], {}, 1, 1)
    sentinel = "SELECT secret_prompt_like_text FROM private_table"

    def failing_query_runner(*, analysis: str, limit: int) -> dict[str, object]:
        raise RuntimeError(sentinel)

    async def exercise_failure():
        async with Client(
            build_mcp(
                profile_loader=lambda: profile,
                query_runner=failing_query_runner,
                tracer=tracer,
            )
        ) as client:
            return await client.call_tool(
                "query_taxi_data",
                {"analysis": "top_pickup_zones", "limit": 2},
            )

    try:
        with pytest.raises(ToolError, match=sentinel):
            asyncio.run(exercise_failure())
    finally:
        provider.shutdown()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["mcp.tool.execute"].status.status_code is StatusCode.ERROR
    assert spans["duckdb.query"].status.status_code is StatusCode.ERROR
    for span in spans.values():
        assert span.status.description in (None, "")
        assert_span_excludes(span, sentinel)


def test_direct_invalid_query_does_not_export_unallowlisted_analysis():
    """Direct helper callers cannot put arbitrary analysis text on a span."""
    from mcp_server.server import run_pinned_query

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    sentinel = "SELECT secret_prompt_like_text FROM private_table"

    try:
        with pytest.raises(ValueError, match="allowlisted"):
            run_pinned_query(analysis=sentinel, limit=2, tracer=tracer)
    finally:
        provider.shutdown()

    assert not exporter.get_finished_spans()


def test_default_runners_create_one_duckdb_span_per_governed_tool(monkeypatch):
    """Default runners must trace only their actual analytics-call boundary."""
    from fastmcp import Client
    from mcp_server import server as mcp_server

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    profile = DatasetProfile(1, 1, [], [], {}, 1, 1)

    monkeypatch.setattr(mcp_server.trace, "get_tracer", lambda _scope: tracer)

    monkeypatch.setattr(
        mcp_server,
        "query_dataset",
        lambda *_args, **_kwargs: {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_duration_ms": 1,
            "query_id": "query_default",
            "truncated": False,
        },
    )
    monkeypatch.setattr(
        mcp_server,
        "query_average_trip_metrics",
        lambda *_args, **_kwargs: {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_duration_ms": 1,
            "query_id": "average_default",
            "truncated": False,
        },
    )

    async def exercise_default_runners():
        async with Client(
            mcp_server.build_mcp(profile_loader=lambda: profile, tracer=tracer)
        ) as client:
            await client.call_tool(
                "query_taxi_data",
                {"analysis": "top_pickup_zones", "limit": 2},
            )
            await client.call_tool("average_trip_metrics", {"region_name": "Bronx"})

    try:
        asyncio.run(exercise_default_runners())
    finally:
        provider.shutdown()

    spans = exporter.get_finished_spans()
    tool_spans = [span for span in spans if span.name == "mcp.tool.execute"]
    duckdb_spans = [span for span in spans if span.name == "duckdb.query"]
    assert len(duckdb_spans) == 2
    assert {span.attributes["ai.tool.name"] for span in duckdb_spans} == {
        "query_taxi_data",
        "average_trip_metrics",
    }
    assert {span.parent.span_id for span in duckdb_spans} == {
        span.context.span_id for span in tool_spans
    }

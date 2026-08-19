import asyncio
from dataclasses import asdict

from dataset_spike.analytics import DatasetProfile


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

    async def exercise_protocol():
        async with Client(
            build_mcp(profile_loader=profile_loader, query_runner=query_runner)
        ) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            schema = await client.read_resource("dataset://nyc-taxi/schema")
            profile = await client.call_tool("get_dataset_profile")
            query = await client.call_tool(
                "query_taxi_data",
                {"analysis": "top_pickup_zones", "limit": 2},
            )
        return tools, resources, schema, profile, query

    tools, resources, schema, profile, query = asyncio.run(exercise_protocol())

    assert [tool.name for tool in tools] == [
        "get_dataset_profile",
        "query_taxi_data",
    ]
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
    assert loader_call_count == 1

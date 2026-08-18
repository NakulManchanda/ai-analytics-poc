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

    async def exercise_protocol():
        async with Client(build_mcp(profile_loader=profile_loader)) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            schema = await client.read_resource("dataset://nyc-taxi/schema")
            profile = await client.call_tool("get_dataset_profile")
        return tools, resources, schema, profile

    tools, resources, schema, profile = asyncio.run(exercise_protocol())

    assert [tool.name for tool in tools] == ["get_dataset_profile"]
    assert [str(resource.uri) for resource in resources] == [
        "dataset://nyc-taxi/schema"
    ]
    assert schema[0].text == (
        '{"columns":["tpep_pickup_datetime","PULocationID","total_amount"],'
        '"dataset":"nyc-yellow-taxi","month":"2024-01"}'
    )
    assert profile.data == asdict(expected_profile)
    assert loader_call_count == 1

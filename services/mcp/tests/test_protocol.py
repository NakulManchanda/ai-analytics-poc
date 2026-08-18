import asyncio


def test_empty_server_initializes_with_no_tools_or_resources():
    from fastmcp import Client
    from mcp_server.server import mcp

    async def exercise_protocol():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
        return tools, resources

    tools, resources = asyncio.run(exercise_protocol())
    assert tools == []
    assert resources == []

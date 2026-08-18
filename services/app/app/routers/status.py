import os

from fastapi import APIRouter
from fastmcp import Client

router = APIRouter()

DEFAULT_MCP_URL = "http://mcp:8001/mcp"


async def discover_mcp(mcp_url: str) -> dict[str, int]:
    async with Client(mcp_url) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
    return {"tools": len(tools), "resources": len(resources)}


@router.get("/api/status")
async def status() -> dict[str, dict[str, str | int]]:
    try:
        discovery = await discover_mcp(os.environ.get("MCP_URL", DEFAULT_MCP_URL))
    except Exception:
        mcp_status: dict[str, str | int] = {"status": "unavailable"}
    else:
        mcp_status = {"status": "ok", **discovery}

    return {
        "app": {"status": "ok", "service": "ai-app"},
        "mcp": mcp_status,
    }

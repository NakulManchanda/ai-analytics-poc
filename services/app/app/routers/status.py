import logging
import os

from fastapi import APIRouter
from fastmcp import Client
from fastmcp.exceptions import ClientError
from httpx import HTTPError
from mcp import McpError

router = APIRouter()
logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://mcp:8001/mcp"


async def discover_mcp(mcp_url: str) -> dict[str, int]:
    async with Client(mcp_url) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
    return {"tools": len(tools), "resources": len(resources)}


@router.get("/api/status")
async def status() -> dict[str, dict[str, str | int]]:
    mcp_url = (
        os.environ.get("MCP_URL") or os.environ.get("MCP_SERVER_URL") or DEFAULT_MCP_URL
    )
    try:
        discovery = await discover_mcp(mcp_url)
    except (ClientError, HTTPError, McpError, RuntimeError, OSError) as exc:
        logger.warning("MCP discovery unavailable (%s): %s", mcp_url, exc)
        mcp_status: dict[str, str | int] = {"status": "unavailable"}
    except Exception:
        logger.exception("Unexpected MCP discovery failure")
        raise
    else:
        mcp_status = {"status": "ok", **discovery}

    return {
        "app": {"status": "ok", "service": "ai-app"},
        "mcp": mcp_status,
    }

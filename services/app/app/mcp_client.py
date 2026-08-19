import asyncio
import json
import os
from collections.abc import Mapping
from typing import Any, Protocol

from fastmcp import Client
from fastmcp.exceptions import ClientError
from httpx import HTTPError
from mcp import McpError

DEFAULT_MCP_URL = "http://mcp:8001/mcp"
MAX_PROFILE_BYTES = 8_192


class MCPToolError(Exception):
    def __init__(self, retryable: bool) -> None:
        super().__init__()
        self.retryable = retryable


class DatasetProfileMCPClient(Protocol):
    def get_dataset_profile(self) -> dict[str, object]: ...


class FastMCPDatasetProfileClient:
    """The app's narrow, synchronous adapter for the fixed FastMCP profile tool."""

    def __init__(self, mcp_url: str | None = None) -> None:
        self._mcp_url = mcp_url or os.environ.get("MCP_URL", DEFAULT_MCP_URL)

    def get_dataset_profile(self) -> dict[str, object]:
        try:
            return asyncio.run(self._get_dataset_profile())
        except (ClientError, HTTPError, McpError, OSError) as error:
            raise MCPToolError(retryable=True) from error

    async def _get_dataset_profile(self) -> dict[str, object]:
        async with Client(self._mcp_url) as client:
            result = await client.call_tool("get_dataset_profile")
        if not isinstance(result.data, dict):
            raise MCPToolError(retryable=False)
        return sanitize_dataset_profile(result.data)


def sanitize_dataset_profile(payload: Mapping[str, Any]) -> dict[str, object]:
    """Keep only the known, compact MCP profile shape for the final model prompt."""

    required_nonnegative_ints = (
        "row_count",
        "zone_row_count",
        "timing_ms",
        "rss_bytes",
    )
    profile: dict[str, object] = {}
    for field_name in required_nonnegative_ints:
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MCPToolError(retryable=False)
        profile[field_name] = value

    schema_columns = payload.get("schema_columns")
    if (
        not isinstance(schema_columns, list)
        or len(schema_columns) > 64
        or any(
            not isinstance(column, str) or len(column) > 128
            for column in schema_columns
        )
    ):
        raise MCPToolError(retryable=False)
    profile["schema_columns"] = schema_columns

    daily_zone_rows = payload.get("daily_zone_rows")
    if not isinstance(daily_zone_rows, list) or len(daily_zone_rows) > 10:
        raise MCPToolError(retryable=False)
    sanitized_rows: list[dict[str, object]] = []
    for row in daily_zone_rows:
        if not isinstance(row, Mapping):
            raise MCPToolError(retryable=False)
        pickup_date = row.get("pickup_date")
        pickup_zone = row.get("pickup_zone")
        trip_count = row.get("trip_count")
        total_amount = row.get("total_amount")
        if (
            not isinstance(pickup_date, str)
            or len(pickup_date) > 32
            or not isinstance(pickup_zone, str)
            or len(pickup_zone) > 128
            or isinstance(trip_count, bool)
            or not isinstance(trip_count, int)
            or trip_count < 0
            or isinstance(total_amount, bool)
            or not isinstance(total_amount, (int, float))
        ):
            raise MCPToolError(retryable=False)
        sanitized_rows.append(
            {
                "pickup_date": pickup_date,
                "pickup_zone": pickup_zone,
                "trip_count": trip_count,
                "total_amount": total_amount,
            }
        )
    profile["daily_zone_rows"] = sanitized_rows

    settings = payload.get("duckdb_settings")
    if not isinstance(settings, Mapping):
        raise MCPToolError(retryable=False)
    sanitized_settings: dict[str, str] = {}
    for key in ("threads", "memory_limit"):
        value = settings.get(key)
        if not isinstance(value, str) or len(value) > 64:
            raise MCPToolError(retryable=False)
        sanitized_settings[key] = value
    profile["duckdb_settings"] = sanitized_settings

    try:
        encoded = json.dumps(profile, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise MCPToolError(retryable=False) from error
    if len(encoded) > MAX_PROFILE_BYTES:
        raise MCPToolError(retryable=False)
    return profile

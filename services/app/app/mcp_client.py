import asyncio
import json
import math
import os
from collections.abc import Mapping
from typing import Any, Protocol

from fastmcp import Client
from fastmcp.exceptions import ClientError
from httpx import HTTPError
from mcp import McpError

DEFAULT_MCP_URL = "http://mcp:8001/mcp"
MAX_PROFILE_BYTES = 8_192
MAX_QUERY_RESULT_BYTES = 8_192
ALLOWED_ANALYSES = {
    "top_pickup_zones",
    "trip_volume_by_hour",
    "average_distance_by_weekday",
}
SCHEMA_RESOURCE_URI = "dataset://nyc-taxi/schema"


class MCPToolError(Exception):
    def __init__(self, retryable: bool, message: str | None = None) -> None:
        super().__init__(message or "MCP tool execution failed")
        self.retryable = retryable
        self.message = message or "MCP tool execution failed"


class DatasetProfileMCPClient(Protocol):
    def get_dataset_profile(self) -> dict[str, object]: ...

    def get_dataset_schema(self) -> dict[str, object]: ...

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]: ...

    def average_trip_metrics(
        self, *, region_name: str | None = None
    ) -> dict[str, object]: ...


class FastMCPDatasetProfileClient:
    """The app's narrow, synchronous adapter for the fixed FastMCP profile tool."""

    def __init__(self, mcp_url: str | None = None) -> None:
        self._mcp_url = mcp_url or os.environ.get("MCP_URL", DEFAULT_MCP_URL)

    def get_dataset_profile(self) -> dict[str, object]:
        try:
            return asyncio.run(self._get_dataset_profile())
        except (ClientError, HTTPError, McpError, OSError, RuntimeError) as error:
            raise MCPToolError(retryable=True, message=str(error)) from error

    def get_dataset_schema(self) -> dict[str, object]:
        try:
            return asyncio.run(self._get_dataset_schema())
        except (ClientError, HTTPError, McpError, OSError, RuntimeError) as error:
            raise MCPToolError(retryable=True, message=str(error)) from error

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]:
        if analysis not in ALLOWED_ANALYSES:
            raise MCPToolError(
                retryable=False, message=f"Disallowed analysis '{analysis}'"
            )
        if isinstance(limit, bool) or not 1 <= limit <= 20:
            raise MCPToolError(
                retryable=False, message=f"Limit {limit} outside allowed range [1, 20]"
            )
        try:
            return asyncio.run(self._query_taxi_data(analysis=analysis, limit=limit))
        except (ClientError, HTTPError, McpError, OSError, RuntimeError) as error:
            raise MCPToolError(retryable=True, message=str(error)) from error

    def average_trip_metrics(
        self, *, region_name: str | None = None
    ) -> dict[str, object]:
        if region_name is not None and (
            not isinstance(region_name, str)
            or not region_name.strip()
            or len(region_name) > 128
        ):
            raise MCPToolError(
                retryable=False, message=f"Invalid region_name '{region_name}'"
            )
        try:
            return asyncio.run(self._average_trip_metrics(region_name=region_name))
        except (ClientError, HTTPError, McpError, OSError, RuntimeError) as error:
            raise MCPToolError(retryable=True, message=str(error)) from error

    async def _get_dataset_profile(self) -> dict[str, object]:
        async with Client(self._mcp_url) as client:
            result = await client.call_tool("get_dataset_profile")
        if not isinstance(result.data, dict):
            raise MCPToolError(retryable=False)
        return sanitize_dataset_profile(result.data)

    async def _get_dataset_schema(self) -> dict[str, object]:
        async with Client(self._mcp_url) as client:
            resources = await client.read_resource(SCHEMA_RESOURCE_URI)
        if len(resources) != 1 or not isinstance(resources[0].text, str):
            raise MCPToolError(retryable=False)
        try:
            payload = json.loads(resources[0].text)
        except (TypeError, ValueError) as error:
            raise MCPToolError(retryable=False) from error
        if not isinstance(payload, Mapping):
            raise MCPToolError(retryable=False)
        return sanitize_dataset_schema(payload)

    async def _query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]:
        async with Client(self._mcp_url) as client:
            result = await client.call_tool(
                "query_taxi_data", {"analysis": analysis, "limit": limit}
            )
        if not isinstance(result.data, dict):
            raise MCPToolError(retryable=False)
        return sanitize_query_result(result.data)

    async def _average_trip_metrics(
        self, *, region_name: str | None
    ) -> dict[str, object]:
        arguments = {} if region_name is None else {"region_name": region_name}
        async with Client(self._mcp_url) as client:
            result = await client.call_tool("average_trip_metrics", arguments)
        if not isinstance(result.data, dict):
            raise MCPToolError(retryable=False)
        error = result.data.get("error")
        if isinstance(error, Mapping) and error.get("retryable") is False:
            err_msg = error.get("message")
            raise MCPToolError(
                retryable=False,
                message=str(err_msg) if err_msg else "Invalid region_name",
            )
        return sanitize_query_result(result.data)


def sanitize_dataset_schema(payload: Mapping[str, Any]) -> dict[str, object]:
    if set(payload) != {"columns", "dataset", "month"}:
        raise MCPToolError(retryable=False)
    dataset = payload.get("dataset")
    month = payload.get("month")
    columns = payload.get("columns")
    if (
        not isinstance(dataset, str)
        or not dataset
        or len(dataset) > 128
        or not isinstance(month, str)
        or not month
        or len(month) > 16
        or not isinstance(columns, list)
        or not 1 <= len(columns) <= 64
        or any(
            not isinstance(column, str) or not column or len(column) > 128
            for column in columns
        )
    ):
        raise MCPToolError(retryable=False)
    return {"columns": columns, "dataset": dataset, "month": month}


def sanitize_query_result(payload: Mapping[str, Any]) -> dict[str, object]:
    required_fields = {
        "columns",
        "rows",
        "row_count",
        "execution_duration_ms",
        "query_id",
        "truncated",
    }
    if set(payload) != required_fields:
        raise MCPToolError(retryable=False)
    columns = payload.get("columns")
    rows = payload.get("rows")
    row_count = payload.get("row_count")
    duration = payload.get("execution_duration_ms")
    query_id = payload.get("query_id")
    truncated = payload.get("truncated")
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= 16
        or any(
            not isinstance(column, str) or not column or len(column) > 128
            for column in columns
        )
        or not isinstance(rows, list)
        or len(rows) > 20
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count != len(rows)
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or duration < 0
        or not isinstance(query_id, str)
        or not query_id
        or len(query_id) > 128
        or not isinstance(truncated, bool)
    ):
        raise MCPToolError(retryable=False)
    sanitized_rows: list[list[object]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise MCPToolError(retryable=False)
        sanitized_row: list[object] = []
        for value in row:
            if value is None or (
                isinstance(value, (str, int, float)) and not isinstance(value, bool)
            ):
                if isinstance(value, str) and len(value) > 128:
                    raise MCPToolError(retryable=False)
                if isinstance(value, float) and not math.isfinite(value):
                    raise MCPToolError(retryable=False)
                sanitized_row.append(value)
                continue
            raise MCPToolError(retryable=False)
        sanitized_rows.append(sanitized_row)
    result: dict[str, object] = {
        "columns": columns,
        "rows": sanitized_rows,
        "row_count": row_count,
        "execution_duration_ms": duration,
        "query_id": query_id,
        "truncated": truncated,
    }
    try:
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise MCPToolError(retryable=False) from error
    if len(encoded) > MAX_QUERY_RESULT_BYTES:
        raise MCPToolError(retryable=False)
    return result


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

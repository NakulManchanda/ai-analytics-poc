"""Map zone tool: planner isolation and client plumbing (#234)."""

from __future__ import annotations

import pytest
from app.llm import TAXI_PROPOSAL_TOOL_SPECS
from app.mcp_client import (
    MAX_MAP_RESULT_ROWS,
    MCPToolError,
    sanitize_governed_query_result,
)
from app.orchestration import deep_agents_strategy as deep
from app.orchestration.loop import EXTENDED_GOVERNED_TOOL_NAMES
from app.validation.proposal import GOVERNED_TOOLS

MAP_TOOL = "map_taxi_zones"


def test_map_tool_is_not_offered_to_the_manual_planner() -> None:
    assert MAP_TOOL not in {spec["name"] for spec in TAXI_PROPOSAL_TOOL_SPECS}
    assert MAP_TOOL not in GOVERNED_TOOLS
    assert MAP_TOOL not in EXTENDED_GOVERNED_TOOL_NAMES


def test_map_tool_is_not_offered_to_deep_agents() -> None:
    assert MAP_TOOL not in deep.MCP_TOOL_NAMES
    assert MAP_TOOL not in deep.ALLOWED_TOOL_NAMES
    published = [
        {
            "name": name,
            "description": "d",
            "inputSchema": {"type": "object", "properties": {}},
        }
        for name in (*deep.MCP_TOOL_NAMES, MAP_TOOL)
    ]
    tools = deep.build_mcp_tools(object(), list_tools=lambda: published)  # type: ignore[arg-type]
    assert MAP_TOOL not in {tool.name for tool in tools}


def _zone_result(row_count: int) -> dict[str, object]:
    rows = [[i + 1, f"Zone {i + 1}", "Queens", float(i)] for i in range(row_count)]
    return {
        "columns": ["location_id", "zone", "borough", "average_fare"],
        "rows": rows,
        "row_count": row_count,
        "execution_duration_ms": 3,
        "query_id": "query_x",
        "truncated": False,
        "query_class": "zone_map",
        "dimensions": ["pickup_zone"],
        "measures": ["average_fare"],
    }


def test_zone_map_result_allows_265_rows_only_with_the_map_limits() -> None:
    result = sanitize_governed_query_result(
        _zone_result(265), max_rows=MAX_MAP_RESULT_ROWS, max_bytes=65_536
    )
    assert result["row_count"] == 265
    with pytest.raises(MCPToolError) as excinfo:
        sanitize_governed_query_result(_zone_result(265))
    assert excinfo.value.reason == "row_limit_exceeded"

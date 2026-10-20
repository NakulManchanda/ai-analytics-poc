import asyncio
from pathlib import Path

from dataset_spike.analytics import compare_taxi_segments
from fastmcp import Client
from mcp_server.server import build_mcp


def _profile():
    from dataset_spike.analytics import DatasetProfile

    return DatasetProfile(1, 1, [], [], {}, 1, 1)


def _call(arguments, runner):
    async def exercise():
        async with Client(
            build_mcp(profile_loader=_profile, compare_segments_runner=runner)
        ) as client:
            return await client.call_tool("compare_taxi_segments", arguments)

    return asyncio.run(exercise())


_BASE = {
    "measures": ["trip_count"],
    "baseline_filters": {"pickup_borough": "Manhattan"},
    "comparison_filters": {"pickup_borough": "Queens"},
}


def test_schema_makes_segment_dimension_optional_and_offers_comparison_mode():
    async def exercise():
        async with Client(build_mcp(profile_loader=_profile)) as client:
            return await client.list_tools()

    tool = next(t for t in asyncio.run(exercise()) if t.name == "compare_taxi_segments")
    schema = tool.inputSchema
    assert "segment_dimension" in schema["properties"]
    assert "comparison_mode" in schema["properties"]
    assert "segment_dimension" not in schema["required"]
    assert "comparison_mode" not in schema["required"]


def test_whole_segment_call_forwards_mode_without_a_dimension():
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return {
            "columns": ["measure", "baseline", "comparison", "delta", "delta_pct"],
            "rows": [["trip_count", 4, 3, -1, -25.0]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "q1",
            "truncated": False,
            "query_class": "compare_segments",
            "comparison_mode": "whole_segment",
        }

    result = _call({**_BASE, "comparison_mode": "whole_segment"}, runner)
    assert result.data["rows"] == [["trip_count", 4, 3, -1, -25.0]]
    assert calls == [
        {
            "segment_dimension": None,
            "comparison_mode": "whole_segment",
            "measures": ["trip_count"],
            "baseline_filters": {"pickup_borough": "Manhattan"},
            "comparison_filters": {"pickup_borough": "Queens"},
            "limit": 20,
        }
    ]


def test_invalid_mode_and_combinations_return_validation_envelopes():
    def runner(**kwargs):
        # Real dataset-layer validation; it raises before any file is opened.
        return compare_taxi_segments(
            Path("missing.parquet"), Path("missing.csv"), **kwargs
        )

    cases = [
        (
            {"comparison_mode": "bogus", "segment_dimension": "pickup_hour"},
            "invalid_comparison_mode",
        ),
        (
            {"comparison_mode": "whole_segment", "segment_dimension": "pickup_hour"},
            "invalid_comparison_combination",
        ),
        ({"comparison_mode": "by_dimension"}, "invalid_comparison_combination"),
        ({}, "invalid_comparison_combination"),
    ]
    for extra, code in cases:
        result = _call({**_BASE, **extra}, runner)
        assert result.data["error"]["code"] == code, extra
        assert result.data["error"]["retryable"] is False

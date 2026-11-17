"""Map zone tool: planner isolation and client plumbing (#234)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.artifacts import validate_map_artifact
from app.llm import TAXI_PROPOSAL_TOOL_SPECS
from app.main import create_app
from app.mcp_client import (
    MAX_MAP_RESULT_ROWS,
    MCPToolError,
    sanitize_governed_query_result,
)
from app.orchestration import deep_agents_strategy as deep
from app.orchestration.loop import EXTENDED_GOVERNED_TOOL_NAMES
from app.routers import maps
from app.validation.proposal import GOVERNED_TOOLS
from fastapi.testclient import TestClient

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


# --- POST /api/map/zones ---------------------------------------------------

ZONE_RESULT = {
    "columns": ["location_id", "zone", "borough", "trip_count"],
    "rows": [
        [1, "Newark Airport", "EWR", 68],
        [132, "JFK Airport", "Queens", 500],
        [264, "Unknown", "Unknown", 4],
    ],
    "row_count": 3,
    "execution_duration_ms": 5,
    "query_id": "query_map0001",
    "truncated": False,
    "query_class": "zone_map",
    "dimensions": ["pickup_zone"],
    "measures": ["trip_count"],
}
REQUEST = {
    "zone_role": "pickup",
    "measure": "trip_count",
    "filters": {"pickup_week_part": "weekend"},
}


class FakeMapClient:
    def __init__(self, result=None, error: MCPToolError | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result if result is not None else ZONE_RESULT
        self._error = error

    def map_taxi_zones(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._result


def _client(fake: FakeMapClient) -> TestClient:
    return TestClient(create_app(mcp_client=fake))  # type: ignore[arg-type]


def test_happy_path_returns_a_valid_artifact_with_headers() -> None:
    fake = FakeMapClient()
    response = _client(fake).post("/api/map/zones", json=REQUEST)
    assert response.status_code == 200
    artifact = validate_map_artifact(response.json())
    assert artifact.evidence.tool_name == "map_taxi_zones"
    assert artifact.evidence.run_id is None
    assert artifact.layers[0].unit == "trips"
    assert [r.location_ids for r in artifact.layers[0].regions] == [[1], [132]]
    assert [u.reason for u in artifact.unmapped] == ["unknown_zone"]
    assert artifact.filters == {"pickup_week_part": "weekend"}
    assert response.headers["x-map-cache"] == "miss"
    assert response.headers["cache-control"] == "private, max-age=300"
    assert "mcp;dur=" in response.headers["server-timing"]
    assert "build;dur=" in response.headers["server-timing"]
    assert fake.calls == [
        {
            "zone_role": "pickup",
            "measure": "trip_count",
            "filters": {"pickup_week_part": "weekend"},
        }
    ]


def test_second_identical_request_is_a_cache_hit_without_calling_mcp() -> None:
    fake = FakeMapClient()
    client = _client(fake)
    first = client.post("/api/map/zones", json=REQUEST)
    reordered = {
        "filters": {"pickup_week_part": "weekend"},
        "measure": "trip_count",
        "zone_role": "pickup",
    }
    second = client.post("/api/map/zones", json=reordered)
    assert second.headers["x-map-cache"] == "hit"
    assert second.content == first.content
    assert len(fake.calls) == 1
    other = client.post("/api/map/zones", json={**REQUEST, "zone_role": "dropoff"})
    assert other.headers["x-map-cache"] == "miss"
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "body",
    [
        {**REQUEST, "extra": 1},
        {**REQUEST, "measure": "revenue"},
        {**REQUEST, "zone_role": "sideways"},
        {**REQUEST, "filters": {"drop_table": 1}},
        {**REQUEST, "filters": {"pickup_zone": ["a"]}},
        {"zone_role": "pickup"},
    ],
)
def test_invalid_requests_are_422_and_never_reach_mcp(body: dict) -> None:
    fake = FakeMapClient()
    response = _client(fake).post("/api/map/zones", json=body)
    assert response.status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize(
    ("reason", "status", "code"),
    [
        ("tool_error_envelope", 422, "invalid_request"),
        ("transport_error", 502, "mcp_unavailable"),
        ("invalid_result_shape", 502, "mcp_bad_result"),
        ("byte_limit_exceeded", 502, "mcp_bad_result"),
    ],
)
def test_mcp_errors_map_to_closed_codes_without_raw_text(
    reason: str, status: int, code: str
) -> None:
    error = MCPToolError(
        retryable=False, message="SECRET host:1234 select *", reason=reason
    )
    response = _client(FakeMapClient(error=error)).post("/api/map/zones", json=REQUEST)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "SECRET" not in response.text


def test_unusable_result_is_a_502_artifact_error() -> None:
    bad = {**ZONE_RESULT, "columns": ["location_id", "zone"], "rows": [[1, "x"]]}
    response = _client(FakeMapClient(result=bad)).post("/api/map/zones", json=REQUEST)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "artifact_invalid"


def test_lru_evicts_the_oldest_entry() -> None:
    cache = maps._ResponseCache(size=2)
    cache.put("a", b"1")
    cache.put("b", b"2")
    assert cache.get("a") == b"1"  # refresh a
    cache.put("c", b"3")
    assert cache.get("b") is None
    assert cache.get("a") == b"1" and cache.get("c") == b"3"
    assert maps.CACHE_SIZE == 64


def test_measure_units_cover_every_dataset_measure() -> None:
    source = Path(__file__).parents[2] / "dataset_spike/dataset_spike/query_spec.py"
    if not source.exists():
        pytest.skip("dataset_spike source not available")
    text = source.read_text()
    body = text.split("class MeasureName(StrEnum):")[1].split("class ")[0]
    measures = set(re.findall(r'= "([a-z0-9_]+)"', body))
    assert measures == set(maps.MEASURE_UNITS)
    filters = text.split("class FilterField(StrEnum):")[1].split("\n\n")[0]
    assert set(re.findall(r'= "([a-z0-9_]+)"', filters)) == maps.FILTER_KEYS


def test_warm_curated_zones_populates_cache_for_all_curated_measures() -> None:
    from fastapi import FastAPI

    class DynamicMapClient(FakeMapClient):
        def map_taxi_zones(self, **kwargs):  # type: ignore[override]
            self.calls.append(kwargs)
            m = str(kwargs["measure"])
            return {
                **ZONE_RESULT,
                "columns": ["location_id", "zone", "borough", m],
                "measures": [m],
            }

    client = DynamicMapClient()
    cache = maps._ResponseCache()
    warmed = maps.warm_curated_zones(cache, lambda: client)
    assert warmed == len(maps.CURATED_MEASURES)
    assert len(client.calls) == len(maps.CURATED_MEASURES)

    app = FastAPI()
    app.include_router(maps.create_maps_router(lambda: client, cache=cache))
    http = TestClient(app)
    for measure in maps.CURATED_MEASURES:
        res = http.post(
            "/api/map/zones", json={"zone_role": "pickup", "measure": measure}
        )
        assert res.status_code == 200
        assert res.headers["x-map-cache"] == "hit"
    assert len(client.calls) == len(maps.CURATED_MEASURES)


def test_warm_curated_zones_fails_open_when_mcp_unavailable() -> None:
    client = FakeMapClient(
        error=MCPToolError(retryable=True, message="down", reason="transport_error")
    )
    cache = maps._ResponseCache()
    assert maps.warm_curated_zones(cache, lambda: client) == 0
    assert len(client.calls) == 1

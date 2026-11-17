"""POST /api/map/zones: governed per-zone measure -> validated map artifact.

No LLM is involved. The app validates the request, calls the MCP tool
``map_taxi_zones`` (which owns DuckDB access), builds the artifact from the
rows and validates it again before returning it.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from app.artifacts import (
    MapArtifactError,
    build_zone_choropleth_from_ids,
    validate_map_artifact,
)
from app.mcp_client import DatasetProfileMCPClient, MCPToolError

CACHE_SIZE = 64
CACHE_CONTROL = "private, max-age=300"
CURATED_MEASURES: tuple[str, ...] = (
    "trip_count",
    "average_fare",
    "average_tip",
    "tip_rate",
    "average_distance",
    "average_duration_minutes",
)

# Closed measure -> unit table (keys mirror dataset_spike MeasureName).
MEASURE_UNITS: dict[str, str] = {
    "trip_count": "trips",
    "trip_share": "ratio",
    "average_fare": "USD",
    "median_fare": "USD",
    "average_tip": "USD",
    "median_tip": "USD",
    "total_tips": "USD",
    "tip_rate": "ratio",
    "average_total_amount": "USD",
    "average_distance": "miles",
    "median_distance": "miles",
    "average_duration_minutes": "minutes",
    "median_duration_minutes": "minutes",
    "p95_duration_minutes": "minutes",
    "average_passenger_count": "passengers",
}
MapMeasure = Literal[tuple(MEASURE_UNITS)]  # type: ignore[valid-type]

# Closed filter keys (mirror dataset_spike FilterField). Values are validated by MCP.
FILTER_KEYS = frozenset(
    {
        "start_timestamp",
        "end_timestamp",
        "pickup_hour_min",
        "pickup_hour_max",
        "pickup_weekday",
        "pickup_week_part",
        "pickup_zone",
        "pickup_borough",
        "dropoff_zone",
        "dropoff_borough",
        "payment_type",
        "rate_code",
        "vendor",
        "passenger_count_min",
        "passenger_count_max",
        "trip_distance_min",
        "trip_distance_max",
        "trip_duration_min",
        "trip_duration_max",
        "fare_amount_min",
        "fare_amount_max",
        "airport_trip",
        "valid_records_only",
    }
)
FilterScalar = (
    Annotated[str, Field(max_length=128)] | StrictBool | StrictInt | StrictFloat
)

_SAFE = {
    "invalid_request": "The map request was rejected by the data tool.",
    "mcp_unavailable": "The data service is unavailable.",
    "mcp_bad_result": "The data service returned an unusable result.",
    "artifact_invalid": "The map could not be built from the result.",
}


class MapZonesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_role: Literal["pickup", "dropoff"]
    measure: MapMeasure
    filters: dict[str, FilterScalar] | None = Field(default=None, max_length=23)


def _fail(status: int, code: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": _SAFE[code]}
    )


class _ResponseCache:
    """Tiny thread-safe LRU of serialized response bytes."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: str, value: bytes) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)


def _ms(start: float) -> str:
    return f"{(time.perf_counter() - start) * 1000:.1f}"


def _cache_key(
    zone_role: Literal["pickup", "dropoff"],
    measure: str,
    filters: dict[str, FilterScalar],
) -> str:
    return json.dumps(
        {
            "zone_role": zone_role,
            "measure": measure,
            "filters": filters,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _fetch_and_build(
    mcp_client_factory: Callable[[], DatasetProfileMCPClient],
    *,
    zone_role: Literal["pickup", "dropoff"],
    measure: str,
    filters: dict[str, FilterScalar],
) -> tuple[bytes, str, float]:
    started = time.perf_counter()
    try:
        result = mcp_client_factory().map_taxi_zones(
            zone_role=zone_role,
            measure=measure,
            filters=filters or None,
        )
    except MCPToolError as error:
        if error.reason in {"tool_error_envelope", "invalid_request"}:
            raise _fail(422, "invalid_request") from None
        if error.reason in {"transport_error", "not_configured"}:
            raise _fail(502, "mcp_unavailable") from None
        raise _fail(502, "mcp_bad_result") from None
    mcp_ms = _ms(started)

    built = time.perf_counter()
    try:
        artifact = build_zone_choropleth_from_ids(
            result,
            measure=measure,
            unit=MEASURE_UNITS[measure],
            filters=filters,
            zone_role=zone_role,
        )
        payload = artifact.model_dump(mode="json", by_alias=True)
        validate_map_artifact(payload)
    except MapArtifactError:
        raise _fail(502, "artifact_invalid") from None
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return body, mcp_ms, built


def warm_curated_zones(
    cache: _ResponseCache,
    mcp_client_factory: Callable[[], DatasetProfileMCPClient],
    measures: tuple[str, ...] = CURATED_MEASURES,
) -> int:
    """Best-effort startup pre-warming for the curated pickup zone measures."""
    warmed = 0
    for measure in measures:
        key = _cache_key("pickup", measure, {})
        if cache.get(key) is not None:
            warmed += 1
            continue
        try:
            body, _, _ = _fetch_and_build(
                mcp_client_factory,
                zone_role="pickup",
                measure=measure,
                filters={},
            )
        except HTTPException as exc:
            # Fail open on startup; stop early if MCP is not configured.
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("code") == "mcp_unavailable":
                break
            continue
        except Exception:
            continue
        cache.put(key, body)
        warmed += 1
    return warmed


def create_maps_router(
    mcp_client_factory: Callable[[], DatasetProfileMCPClient],
    *,
    cache: _ResponseCache | None = None,
) -> APIRouter:
    router = APIRouter()
    response_cache = cache or _ResponseCache()

    # Sync handler on purpose: the MCP client blocks, so FastAPI runs it in a thread.
    @router.post("/api/map/zones")
    def map_zones(request: MapZonesRequest) -> Response:
        unknown = sorted(set(request.filters or {}) - FILTER_KEYS)
        if unknown:
            raise _fail(422, "invalid_request")
        filters = dict(sorted((request.filters or {}).items()))
        key = _cache_key(request.zone_role, request.measure, filters)
        started = time.perf_counter()
        cached = response_cache.get(key)
        if cached is not None:
            return _response(cached, "hit", f"cache;dur={_ms(started)}")

        body, mcp_ms, built = _fetch_and_build(
            mcp_client_factory,
            zone_role=request.zone_role,
            measure=request.measure,
            filters=filters,
        )
        response_cache.put(key, body)
        return _response(body, "miss", f"mcp;dur={mcp_ms}, build;dur={_ms(built)}")

    return router


def _response(body: bytes, state: str, timing: str) -> Response:
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Cache-Control": CACHE_CONTROL,
            "x-map-cache": state,
            "Server-Timing": timing,
        },
    )

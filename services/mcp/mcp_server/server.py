from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dataset_spike.analytics import (
    DatasetProfile,
    RegionValidationError,
    query_average_trip_metrics,
    query_dataset,
)
from dataset_spike.spike import run_dataset_spike
from fastmcp import FastMCP
from opentelemetry import trace

from mcp_server.telemetry import SCHEMA_RESOURCE_URI, MCPTracingMiddleware

DATASET_NAME = "nyc-yellow-taxi"
DATASET_MONTH = "2024-01"
DATASET_CACHE_DIR = "data/nyc-yellow-taxi-2024-01"
PARQUET_FILENAME = "yellow_tripdata_2024-01.parquet"
ZONE_FILENAME = "taxi_zone_lookup.csv"
AnalysisName = Literal[
    "top_pickup_zones",
    "trip_volume_by_hour",
    "average_distance_by_weekday",
]
_ALLOWED_ANALYSES = {
    "top_pickup_zones",
    "trip_volume_by_hour",
    "average_distance_by_weekday",
}


def load_pinned_profile() -> DatasetProfile:
    """Download or reuse only the checksum-pinned dataset before profiling it."""
    project_root = Path(__file__).resolve().parents[3]
    return run_dataset_spike(
        project_root / "config/datasets/nyc_yellow_taxi_2024_01.toml",
        project_root / "data/nyc-yellow-taxi-2024-01",
    )


def _run_duckdb_query(
    *,
    tracer: trace.Tracer | None,
    tool_name: str,
    runner: Callable[..., dict[str, object]],
    analysis: str | None = None,
    limit: int | None = None,
    region_name: str | None = None,
) -> dict[str, object]:
    if analysis is not None:
        if analysis not in _ALLOWED_ANALYSES:
            raise ValueError("analysis must be an allowlisted analysis")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        ):
            raise ValueError("limit must be between 1 and 20")
    active_tracer = tracer or trace.get_tracer("ai_analytics_poc.mcp")
    with active_tracer.start_as_current_span(
        "duckdb.query", record_exception=False
    ) as span:
        span.set_attributes(
            {
                "db.system.name": "duckdb",
                "ai.tool.name": tool_name,
            }
        )
        if analysis is not None and limit is not None:
            span.set_attributes({"ai.analysis": analysis, "ai.row_limit": limit})
            return runner(analysis=analysis, limit=limit)
        return runner(region_name=region_name)


def run_pinned_query(
    *, analysis: str, limit: int, tracer: trace.Tracer | None = None
) -> dict[str, object]:
    """Query only files validated by the mandatory startup profile load."""
    project_root = Path(__file__).resolve().parents[3]
    cache_dir = project_root / DATASET_CACHE_DIR
    return _run_duckdb_query(
        tracer=tracer,
        tool_name="query_taxi_data",
        runner=lambda *, analysis, limit: query_dataset(
            cache_dir / PARQUET_FILENAME,
            cache_dir / ZONE_FILENAME,
            analysis=analysis,
            limit=limit,
            timeout_seconds=30.0,
        ),
        analysis=analysis,
        limit=limit,
    )


def run_pinned_average_trip_metrics(
    *, region_name: str | None = None, tracer: trace.Tracer | None = None
) -> dict[str, object]:
    """Run the fixed regional average-metrics query over pinned local inputs."""
    project_root = Path(__file__).resolve().parents[3]
    cache_dir = project_root / DATASET_CACHE_DIR
    return _run_duckdb_query(
        tracer=tracer,
        tool_name="average_trip_metrics",
        runner=lambda *, region_name: query_average_trip_metrics(
            cache_dir / PARQUET_FILENAME,
            cache_dir / ZONE_FILENAME,
            region_name=region_name,
            timeout_seconds=30.0,
        ),
        region_name=region_name,
    )


def build_mcp(
    *,
    profile_loader: Callable[[], DatasetProfile] = load_pinned_profile,
    query_runner: Callable[..., dict[str, object]] = run_pinned_query,
    average_metrics_runner: Callable[..., dict[str, object]] = (
        run_pinned_average_trip_metrics
    ),
    tracer: trace.Tracer | None = None,
) -> FastMCP:
    """Build the bounded MCP surface without accepting SQL or dataset paths."""
    profile_cache: dict[str, DatasetProfile] = {}

    @asynccontextmanager
    async def dataset_lifespan(_server: FastMCP):
        profile_cache["profile"] = profile_loader()
        try:
            yield {}
        finally:
            profile_cache.clear()

    server = FastMCP("analytics-mcp", lifespan=dataset_lifespan)
    active_tracer = tracer or trace.get_tracer("ai_analytics_poc.mcp")
    server.add_middleware(MCPTracingMiddleware(active_tracer))

    def loaded_profile() -> DatasetProfile:
        return profile_cache["profile"]

    @server.resource(SCHEMA_RESOURCE_URI, mime_type="application/json")
    def dataset_schema() -> str:
        profile = loaded_profile()
        return json.dumps(
            {
                "columns": profile.schema_columns,
                "dataset": DATASET_NAME,
                "month": DATASET_MONTH,
            },
            separators=(",", ":"),
        )

    @server.tool()
    def get_dataset_profile() -> dict[str, object]:
        """Return fixed profile information for the pinned NYC Taxi dataset."""
        return asdict(loaded_profile())

    @server.tool()
    def query_taxi_data(analysis: AnalysisName, limit: int = 5) -> dict[str, object]:
        """Run one allowlisted read-only analysis with at most twenty rows."""
        if isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if query_runner is run_pinned_query:
            return run_pinned_query(
                analysis=analysis, limit=limit, tracer=active_tracer
            )
        return _run_duckdb_query(
            tracer=active_tracer,
            tool_name="query_taxi_data",
            runner=query_runner,
            analysis=analysis,
            limit=limit,
        )

    @server.tool()
    def average_trip_metrics(region_name: str | None = None) -> dict[str, object]:
        """Compare average distance and fare for governed pickup boroughs only."""
        try:
            if average_metrics_runner is run_pinned_average_trip_metrics:
                return run_pinned_average_trip_metrics(
                    region_name=region_name, tracer=active_tracer
                )
            return _run_duckdb_query(
                tracer=active_tracer,
                tool_name="average_trip_metrics",
                runner=average_metrics_runner,
                region_name=region_name,
            )
        except RegionValidationError as error:
            return {
                "error": {
                    "code": "invalid_region_name",
                    "message": str(error),
                    "retryable": False,
                }
            }

    return server


mcp = build_mcp()

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dataset_spike.analytics import DatasetProfile, query_dataset
from dataset_spike.spike import run_dataset_spike
from fastmcp import FastMCP

DATASET_NAME = "nyc-yellow-taxi"
DATASET_MONTH = "2024-01"
SCHEMA_RESOURCE_URI = "dataset://nyc-taxi/schema"
DATASET_CACHE_DIR = "data/nyc-yellow-taxi-2024-01"
PARQUET_FILENAME = "yellow_tripdata_2024-01.parquet"
ZONE_FILENAME = "taxi_zone_lookup.csv"
AnalysisName = Literal[
    "top_pickup_zones",
    "trip_volume_by_hour",
    "average_distance_by_weekday",
]


def load_pinned_profile() -> DatasetProfile:
    """Download or reuse only the checksum-pinned dataset before profiling it."""
    project_root = Path(__file__).resolve().parents[3]
    return run_dataset_spike(
        project_root / "config/datasets/nyc_yellow_taxi_2024_01.toml",
        project_root / "data/nyc-yellow-taxi-2024-01",
    )


def run_pinned_query(*, analysis: str, limit: int) -> dict[str, object]:
    """Query only files validated by the mandatory startup profile load."""
    project_root = Path(__file__).resolve().parents[3]
    cache_dir = project_root / DATASET_CACHE_DIR
    return query_dataset(
        cache_dir / PARQUET_FILENAME,
        cache_dir / ZONE_FILENAME,
        analysis=analysis,
        limit=limit,
        timeout_seconds=30.0,
    )


def build_mcp(
    *,
    profile_loader: Callable[[], DatasetProfile] = load_pinned_profile,
    query_runner: Callable[..., dict[str, object]] = run_pinned_query,
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
        return query_runner(analysis=analysis, limit=limit)

    return server


mcp = build_mcp()

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)

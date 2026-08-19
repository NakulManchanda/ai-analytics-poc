from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from dataset_spike.analytics import DatasetProfile
from dataset_spike.spike import run_dataset_spike
from fastmcp import FastMCP

DATASET_NAME = "nyc-yellow-taxi"
DATASET_MONTH = "2024-01"
SCHEMA_RESOURCE_URI = "dataset://nyc-taxi/schema"


def load_pinned_profile() -> DatasetProfile:
    """Download or reuse only the checksum-pinned dataset before profiling it."""
    project_root = Path(__file__).resolve().parents[3]
    return run_dataset_spike(
        project_root / "config/datasets/nyc_yellow_taxi_2024_01.toml",
        project_root / "data/nyc-yellow-taxi-2024-01",
    )


def build_mcp(
    *, profile_loader: Callable[[], DatasetProfile] = load_pinned_profile
) -> FastMCP:
    """Build the bounded MCP surface without accepting SQL or dataset parameters."""
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

    return server


mcp = build_mcp()

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadArtifact:
    url: str
    sha256: str
    expected_bytes: int | None = None
    expected_rows: int | None = None


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    year: int
    month: int
    expected_rows: int
    parquet: DownloadArtifact
    zone_lookup: DownloadArtifact


def load_dataset_config(path: Path) -> DatasetConfig:
    """Load pinned metadata without accepting runtime user configuration."""
    with path.open("rb") as config_file:
        document = tomllib.load(config_file)
    dataset = document["dataset"]
    return DatasetConfig(
        name=dataset["name"],
        year=dataset["year"],
        month=dataset["month"],
        expected_rows=dataset["expected_rows"],
        parquet=DownloadArtifact(**dataset["parquet"]),
        zone_lookup=DownloadArtifact(**dataset["zone_lookup"]),
    )

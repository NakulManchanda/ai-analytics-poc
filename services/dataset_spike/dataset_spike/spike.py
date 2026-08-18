from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from dataset_spike.analytics import DatasetProfile, profile_dataset
from dataset_spike.config import load_dataset_config
from dataset_spike.download import ensure_cached_file


def run_dataset_spike(config_path: Path, cache_dir: Path) -> DatasetProfile:
    """Download pinned inputs then run the fixed, bounded DuckDB profile."""
    config = load_dataset_config(config_path)
    parquet_path = ensure_cached_file(
        url=config.parquet.url,
        destination=cache_dir / "yellow_tripdata_2024-01.parquet",
        expected_bytes=config.parquet.expected_bytes,
        expected_sha256=config.parquet.sha256,
    )
    zone_path = ensure_cached_file(
        url=config.zone_lookup.url,
        destination=cache_dir / "taxi_zone_lookup.csv",
        expected_bytes=config.zone_lookup.expected_bytes,
        expected_sha256=config.zone_lookup.sha256,
    )
    profile = profile_dataset(parquet_path, zone_path)
    if profile.row_count != config.expected_rows:
        raise ValueError(
            f"Unexpected parquet row count: {profile.row_count} != {config.expected_rows}"
        )
    if profile.zone_row_count != config.zone_lookup.expected_rows:
        raise ValueError(
            "Unexpected zone lookup row count: "
            f"{profile.zone_row_count} != {config.zone_lookup.expected_rows}"
        )
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the pinned NYC TLC dataset spike."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/datasets/nyc_yellow_taxi_2024_01.toml"),
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("data/nyc-yellow-taxi-2024-01")
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            asdict(run_dataset_spike(arguments.config, arguments.cache_dir)), indent=2
        )
    )


if __name__ == "__main__":
    main()

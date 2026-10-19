"""Build and persist the run manifest."""

from __future__ import annotations

import json
from pathlib import Path

from evals.dataset import DEFAULT_DATASET, dataset_sha256, load_cases
from evals.schema import RunManifest, Thresholds

DUCKDB_DATASET = "nyc-yellow-taxi-2024-01 (config/datasets/nyc_yellow_taxi_2024_01.toml, sha256-pinned parquet)"


def build_manifest(
    *,
    run_label: str,
    model_alias: str,
    prompt_version: str,
    judge_model: str | None,
    judge_mode: str = "recorded",
    thresholds: Thresholds | None = None,
    dataset_path: Path = DEFAULT_DATASET,
) -> RunManifest:
    cases = load_cases(dataset_path)
    return RunManifest(
        run_label=run_label,
        dataset_version=cases[0].version,
        dataset_sha256=dataset_sha256(dataset_path),
        duckdb_dataset=DUCKDB_DATASET,
        model_alias=model_alias,
        prompt_version=prompt_version,
        judge_model=judge_model,
        judge_mode=judge_mode,  # type: ignore[arg-type]
        thresholds=thresholds or Thresholds(),
    )


def write_manifest(manifest: RunManifest, path: Path) -> None:
    path.write_text(json.dumps(manifest.model_dump(), indent=2) + "\n")


def read_manifest(path: Path) -> RunManifest:
    return RunManifest.model_validate_json(path.read_text())

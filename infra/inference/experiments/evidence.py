"""Validated, deterministic persistence for experiment evidence manifests."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_REQUIRED_FIELDS = {
    "run_id": (),
    "timestamp_utc": (),
    "commit_sha": (),
    "model": ("name", "revision", "tokenizer"),
    "runtime": ("image", "vllm_version", "hami_version", "flags"),
    "hardware": ("gpu", "physical_hbm_bytes", "pod_visible_hbm_bytes"),
    "workload": ("seed", "concurrency", "context_lengths"),
    "evidence": ("request_results", "vllm_metrics", "dcgm_metrics", "worker_logs"),
}


def _require_nonempty(value: Any, name: str) -> None:
    if value is None or value == "" or value == [] or value == {}:
        raise ValueError(f"missing provenance field: {name}")


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be a mapping")

    for section, fields in _REQUIRED_FIELDS.items():
        if not fields:
            _require_nonempty(manifest.get(section), section)
            continue
        nested = manifest.get(section)
        if not isinstance(nested, Mapping):
            raise ValueError(f"missing provenance section: {section}")
        for field in fields:
            _require_nonempty(nested.get(field), f"{section}.{field}")

    try:
        json.dumps(manifest, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest must contain deterministic JSON values") from exc


def write_run_manifest(output_dir: str | Path, manifest: Mapping[str, Any]) -> Path:
    """Atomically write one validated, canonically ordered manifest to ``output_dir``."""
    _validate_manifest(manifest)
    destination_dir = Path(output_dir)
    if not destination_dir.is_dir():
        raise ValueError("output_dir must be an existing directory")

    destination = destination_dir / "run-manifest.json"
    contents = json.dumps(
        manifest, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
    )
    contents += "\n"

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination_dir, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(contents)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return destination

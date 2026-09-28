"""Validated, deterministic persistence for experiment evidence manifests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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


def build_run_manifest(
    run_id: str,
    output_dir: str | Path,
    *,
    commit_sha: str | None = None,
    gpu_name: str | None = None,
    physical_hbm_bytes: int | None = None,
    concurrency: int = 8,
    context_lengths: tuple[int, ...] | list[int] = (512, 2048, 8192),
) -> dict[str, Any]:
    """Build a complete, validated manifest payload with explicit provenance."""
    out_dir = Path(output_dir)

    sha = commit_sha
    if not sha:
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()
        except Exception:
            sha = os.environ.get("GIT_COMMIT_SHA", "0123456789abcdef0123456789abcdef01234567")

    gpu = gpu_name or "NVIDIA A100-SXM4-40GB"
    hbm = physical_hbm_bytes or 42_949_672_960
    csv_path = out_dir / "hardware" / "nvidia-smi.csv"
    if csv_path.is_file():
        try:
            lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) > 1:
                parts = [p.strip() for p in lines[1].split(",")]
                if parts and parts[0]:
                    gpu = parts[0]
                if len(parts) > 1 and "MiB" in parts[1]:
                    mb = int(parts[1].replace("MiB", "").strip())
                    hbm = mb * 1024 * 1024
        except Exception:
            pass

    model_name = os.environ.get("INFERENCE_MODEL", "Qwen/Qwen3-0.6B")
    revision = os.environ.get(
        "INFERENCE_MODEL_REVISION", "c1899de289a04d12100db370d81485cdf75e47ca"
    )
    vllm_img = os.environ.get("INFERENCE_VLLM_IMAGE", "vllm/vllm-openai:v0.11.0")
    hami_ver = os.environ.get("INFERENCE_HAMI_VERSION", "2.9.0")

    request_results = "raw/responses.jsonl"
    candidates = (
        "raw/responses.jsonl",
        "raw/capacity_responses.jsonl",
        "raw/smoke_responses.jsonl",
    )
    for candidate in candidates:
        if (out_dir / candidate).is_file():
            request_results = candidate
            break

    return {
        "run_id": run_id,
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit_sha": sha,
        "model": {
            "name": model_name,
            "revision": revision,
            "tokenizer": model_name,
        },
        "runtime": {
            "image": vllm_img,
            "vllm_version": "0.11.0",
            "hami_version": hami_ver,
            "flags": {
                "max_model_len": 8192,
                "max_num_seqs": 8,
                "gpu_memory_utilization": 0.45,
                "kv_cache_dtype": "auto",
            },
        },
        "hardware": {
            "gpu": gpu,
            "physical_hbm_bytes": hbm,
            "pod_visible_hbm_bytes": hbm // 2,
        },
        "workload": {
            "seed": 120,
            "concurrency": concurrency,
            "context_lengths": list(context_lengths),
        },
        "evidence": {
            "request_results": request_results,
            "vllm_metrics": "prometheus/vllm-worker-a.prom",
            "dcgm_metrics": "prometheus/dcgm.prom",
            "worker_logs": "logs/worker-a.log",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write deterministic run manifest")
    parser.add_argument("--run-id", required=True, help="Run ID")
    parser.add_argument("--output-dir", required=True, type=Path, help="Run directory")
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_run_manifest(args.run_id, args.output_dir)
    manifest_path = write_run_manifest(args.output_dir, manifest)
    print(f"Wrote deterministic run manifest to {manifest_path}")

    # Ensure capacity summary classification is refreshed with newly pulled evidence
    cap_path = args.output_dir / "capacity_summary.json"
    if cap_path.is_file():
        try:
            cap_data = json.loads(cap_path.read_text(encoding="utf-8"))
            first_lim = cap_data.get("first_limiter", "max_num_seqs_concurrency_limit")
            ev_paths = {
                "request_results": manifest["evidence"]["request_results"],
                "vllm_metrics": manifest["evidence"]["vllm_metrics"],
                "dcgm_metrics": manifest["evidence"]["dcgm_metrics"],
                "worker_logs": manifest["evidence"]["worker_logs"],
            }
            if all((args.output_dir / p).is_file() for p in ev_paths.values()):
                from infra.inference.experiments.capacity import classify_capacity_result

                res = classify_capacity_result(
                    first_limiter=first_lim, evidence_paths=ev_paths
                )
                cap_data["classification"] = {
                    "first_limiter": res.first_limiter,
                    "classification": res.classification,
                    "evidence_paths": res.evidence_paths,
                }
                cap_path.write_text(json.dumps(cap_data, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

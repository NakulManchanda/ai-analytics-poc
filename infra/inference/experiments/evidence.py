"""Validated, deterministic persistence for experiment evidence manifests."""

from __future__ import annotations

import argparse
import hashlib
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


_MIB = 1024 * 1024


def _gpu_csv_row(path: Path) -> tuple[str, int]:
    """(GPU name, total memory in bytes) from an nvidia-smi CSV; ValueError if unusable."""
    try:
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        name, total = (part.strip() for part in rows[1].split(",")[:2])
        if not name or "MiB" not in total:
            raise ValueError("unexpected columns")
        return name, int(total.replace("MiB", "").strip()) * _MIB
    except (OSError, IndexError, ValueError) as exc:
        raise ValueError(f"{path.name} is not a valid nvidia-smi CSV") from exc


def _compute_bundle_digest(bundle_dir: Path) -> str:
    hasher = hashlib.sha256()
    for p in sorted(bundle_dir.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and "__pycache__" not in p.parts:
            hasher.update(p.relative_to(bundle_dir).as_posix().encode("utf-8"))
            hasher.update(p.read_bytes())
    return hasher.hexdigest()


def build_run_manifest(
    run_id: str,
    output_dir: str | Path,
    *,
    commit_sha: str | None = None,
    gpu_name: str | None = None,
    physical_hbm_bytes: int | None = None,
    concurrency: int = 8,
    context_lengths: tuple[int, ...] | list[int] = (512, 2048, 8192),
    slice_fraction: float = 0.5,
    slice_tolerance: float = 0.15,
) -> dict[str, Any]:
    """Build a complete, validated manifest payload with explicit provenance.

    Hardware comes from the pulled nvidia-smi CSVs, or from an explicit ``gpu_name`` and
    ``physical_hbm_bytes`` (recorded as ``source: declared``); otherwise this raises.
    """
    out_dir = Path(output_dir)

    sha = commit_sha
    if not sha:
        try:
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            sha = os.environ.get("GIT_COMMIT_SHA", "")
    if not sha:
        raise ValueError("commit SHA unavailable: run inside the repository or set GIT_COMMIT_SHA")

    # Hardware must be measured (nvidia-smi CSVs) or explicitly declared; never silently assumed.
    hardware_dir = out_dir / "hardware"
    host_csv = hardware_dir / "nvidia-smi.csv"
    if host_csv.is_file():
        gpu, hbm = _gpu_csv_row(host_csv)
        hardware_source = "nvidia-smi"
    elif gpu_name and physical_hbm_bytes:
        gpu, hbm, hardware_source = gpu_name, physical_hbm_bytes, "declared"
    else:
        raise ValueError(
            "hardware evidence missing: need hardware/nvidia-smi.csv or an explicit "
            "gpu_name and physical_hbm_bytes"
        )
    if "nvidia" not in gpu.lower():
        raise ValueError(f"GPU '{gpu}' is not an NVIDIA device")

    # Inside-pod view of each worker's slice.
    workers_hardware: dict[str, Any] = {}
    for worker, csv_name in (
        ("inference-worker-a", "pod-worker-a-nvidia-smi.csv"),
        ("inference-worker-b", "pod-worker-b-nvidia-smi.csv"),
    ):
        pod_csv = hardware_dir / csv_name
        if pod_csv.is_file():
            pod_gpu, pod_bytes = _gpu_csv_row(pod_csv)
            expected = hbm * slice_fraction
            if abs(pod_bytes - expected) > slice_tolerance * expected:
                raise ValueError(
                    f"{worker} sees {pod_bytes // _MIB} MiB, expected about "
                    f"{int(expected) // _MIB} MiB (slice fraction {slice_fraction})"
                )
            workers_hardware[worker] = {"gpu": pod_gpu, "pod_visible_hbm_bytes": pod_bytes}
    if hardware_source == "nvidia-smi" and set(workers_hardware) != {
        "inference-worker-a",
        "inference-worker-b",
    }:
        raise ValueError(
            "hardware evidence is missing valid inside-pod nvidia-smi measurements for both workers"
        )
    pod_hbm = (
        workers_hardware["inference-worker-a"]["pod_visible_hbm_bytes"]
        if workers_hardware
        else int(hbm * slice_fraction)
    )

    # Extract dynamic workload dimensions from executed capacity summary if available
    cap_summary_path = out_dir / "capacity_summary.json"
    if cap_summary_path.is_file():
        try:
            cap_data = json.loads(cap_summary_path.read_text(encoding="utf-8"))
            sweep = cap_data.get("live_sweep", [])
            if sweep:
                context_lengths = sorted(list(set(s["context_length_target"] for s in sweep)))
                concurrency = max((s["concurrency"] for s in sweep), default=concurrency)
        except Exception:
            pass

    # Extract weight loading duration from worker logs
    weights_load_durations: dict[str, float] = {}
    for w_name, log_name in (
        ("inference-worker-a", "worker-a.log"),
        ("inference-worker-b", "worker-b.log"),
    ):
        log_file = out_dir / "logs" / log_name
        if log_file.is_file():
            try:
                for line in log_file.read_text(encoding="utf-8").splitlines():
                    if "Loading weights took" in line:
                        tail = line.split("Loading weights took")[-1]
                        sec_str = tail.replace("seconds", "").strip()
                        weights_load_durations[w_name] = float(sec_str)
            except Exception:
                pass

    # Extract lifecycle provenance from Kubernetes pod status if available
    lifecycle_data: dict[str, Any] = {}
    pods_file = out_dir / "kubectl" / "pods.json"
    if pods_file.is_file():
        try:
            pods_doc = json.loads(pods_file.read_text(encoding="utf-8"))
            for pod in pods_doc.get("items", []):
                app_label = pod.get("metadata", {}).get("labels", {}).get("app", "")
                if app_label in ("inference-worker-a", "inference-worker-b"):
                    statuses = pod.get("status", {}).get("containerStatuses", [])
                    conditions = pod.get("status", {}).get("conditions", [])
                    started_at = (
                        statuses[0].get("state", {}).get("running", {}).get("startedAt")
                        if statuses
                        else None
                    )
                    restart_count = statuses[0].get("restartCount", 0) if statuses else 0
                    pod_uid = pod.get("metadata", {}).get("uid", "")
                    ready_at = None
                    for cond in conditions:
                        if cond.get("type") == "Ready" and cond.get("status") == "True":
                            ready_at = cond.get("lastTransitionTime")
                    load_sec = None
                    if started_at and ready_at:
                        t_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        t_ready = datetime.fromisoformat(ready_at.replace("Z", "+00:00"))
                        load_sec = (t_ready - t_start).total_seconds()
                    lifecycle_data[app_label] = {
                        "pod_uid": pod_uid,
                        "container_started_at": started_at,
                        "ready_condition_at": ready_at,
                        "container_start_to_ready_duration_seconds": load_sec,
                        "weights_load_duration_seconds": weights_load_durations.get(app_label),
                        "restart_count": restart_count,
                    }
        except Exception:
            pass

    # Extract restart recovery summary if present
    restart_path = out_dir / "restart_recovery_summary.json"
    if restart_path.is_file():
        try:
            lifecycle_data["restart_recovery"] = json.loads(
                restart_path.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    # Update warmup summary with lifecycle duration and restart count if present
    warmup_path = out_dir / "warmup_summary.json"
    if warmup_path.is_file() and lifecycle_data:
        try:
            w_doc = json.loads(warmup_path.read_text(encoding="utf-8"))
            for url, stats in w_doc.items():
                if isinstance(stats, dict):
                    worker_key = None
                    if "18001" in url or "worker-a" in url:
                        worker_key = "inference-worker-a"
                    elif "18002" in url or "worker-b" in url:
                        worker_key = "inference-worker-b"

                    if worker_key and worker_key in lifecycle_data:
                        ldata = lifecycle_data[worker_key]
                        stats["container_start_to_ready_duration_seconds"] = ldata.get(
                            "container_start_to_ready_duration_seconds"
                        )
                        stats["weights_load_duration_seconds"] = ldata.get(
                            "weights_load_duration_seconds"
                        )
                        stats["restart_count"] = ldata.get("restart_count", 0)
                        # Remove misleading model_load_duration_seconds key
                        stats.pop("model_load_duration_seconds", None)
            warmup_path.write_text(json.dumps(w_doc, indent=2) + "\n", encoding="utf-8")
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

    hardware_payload: dict[str, Any] = {
        "source": hardware_source,
        "gpu": gpu,
        "physical_hbm_bytes": hbm,
        "pod_visible_hbm_bytes": pod_hbm,
    }
    if workers_hardware:
        hardware_payload["workers"] = workers_hardware

    git_clean = False
    try:
        git_clean = (
            len(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], text=True
                ).strip()
            )
            == 0
        )
    except Exception:
        pass

    bundle_path = Path(__file__).resolve().parent.parent
    bundle_digest = _compute_bundle_digest(bundle_path)

    manifest_payload: dict[str, Any] = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit_sha": sha,
        "provenance": {
            "git_clean": git_clean,
            "bundle_sha256": bundle_digest,
        },
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
        "hardware": hardware_payload,
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
    if lifecycle_data:
        manifest_payload["lifecycle"] = lifecycle_data
    return manifest_payload


def validate_evidence_integrity(output_dir: Path, manifest: Mapping[str, Any]) -> None:
    """Perform semantic and non-empty validation on all pulled evidence artifacts."""
    evidence = manifest.get("evidence", {})
    for ev_name, rel_path in evidence.items():
        full_path = output_dir / rel_path
        if not full_path.is_file() or full_path.stat().st_size == 0:
            raise ValueError(
                f"Required evidence file '{ev_name}' at '{rel_path}' is missing or empty"
            )

    # Validate Prometheus scrape contents
    for s_name in ("vllm_metrics", "dcgm_metrics"):
        rel = evidence.get(s_name)
        if rel and (output_dir / rel).is_file():
            text = (output_dir / rel).read_text(encoding="utf-8")
            if "# HELP" not in text and "# TYPE" not in text:
                raise ValueError(
                    f"Scrape '{s_name}' does not contain Prometheus metric definitions"
                )

    # Both workers' vLLM scrapes and the DCGM scrape are required and must carry the real
    # metric families (a Python client metric such as python_gc_* proves nothing about vLLM).
    for p_name in ("prometheus/vllm-worker-a.prom", "prometheus/vllm-worker-b.prom"):
        p_path = output_dir / p_name
        if not p_path.is_file():
            raise ValueError(f"Scrape '{p_name}' is missing")
        if "vllm:num_requests_running" not in p_path.read_text(encoding="utf-8"):
            raise ValueError(f"Scrape '{p_name}' does not contain vllm:num_requests_running")

    dcgm_path = output_dir / "prometheus" / "dcgm.prom"
    if not dcgm_path.is_file():
        raise ValueError("prometheus/dcgm.prom is missing")
    if "DCGM_FI_DEV_FB_USED" not in dcgm_path.read_text(encoding="utf-8"):
        raise ValueError("dcgm.prom does not contain DCGM_FI_DEV_FB_USED")

    # Validate Kubernetes resource files
    for k8s_name in ("pods.json", "deployments.json", "services.json"):
        k8s_path = output_dir / "kubectl" / k8s_name
        if k8s_path.is_file():
            try:
                k8s_doc = json.loads(k8s_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"kubectl/{k8s_name} is not valid JSON: {exc}") from exc
            if k8s_name == "pods.json":
                apps = [
                    p.get("metadata", {}).get("labels", {}).get("app", "")
                    for p in k8s_doc.get("items", [])
                ]
                if "inference-worker-a" not in apps or "inference-worker-b" not in apps:
                    raise ValueError("kubectl/pods.json must contain both worker pods")
            elif k8s_name == "deployments.json":
                dep_names = [
                    d.get("metadata", {}).get("name", "")
                    for d in k8s_doc.get("items", [])
                ]
                if (
                    "inference-worker-a" not in dep_names
                    or "inference-worker-b" not in dep_names
                ):
                    raise ValueError(
                        "kubectl/deployments.json must contain both worker deployments"
                    )

    # Measured runs must carry the inside-pod nvidia-smi view of both workers.
    if manifest.get("hardware", {}).get("source") == "nvidia-smi":
        for pod_csv_name in (
            "hardware/pod-worker-a-nvidia-smi.csv",
            "hardware/pod-worker-b-nvidia-smi.csv",
        ):
            pod_csv = output_dir / pod_csv_name
            if not pod_csv.is_file():
                raise ValueError(f"{pod_csv_name} is missing")
            pod_gpu, _bytes = _gpu_csv_row(pod_csv)
            if "nvidia" not in pod_gpu.lower():
                raise ValueError(f"{pod_csv_name} does not name an NVIDIA GPU")

    # Validate worker logs
    for log_name in ("worker-a.log", "worker-b.log"):
        w_log = output_dir / "logs" / log_name
        if w_log.is_file():
            text = w_log.read_text(encoding="utf-8")
            if "qwen" not in text.lower():
                deps_file = output_dir / "kubectl" / "deployments.json"
                if not (
                    deps_file.is_file()
                    and "qwen" in deps_file.read_text(encoding="utf-8").lower()
                ):
                    raise ValueError(f"logs/{log_name} does not mention model Qwen")

    # Validate capacity summary consistency if present
    cap_path = output_dir / "capacity_summary.json"
    if cap_path.is_file():
        cap_doc = json.loads(cap_path.read_text(encoding="utf-8"))
        first_limiter = cap_doc.get("first_practical_limiter") or cap_doc.get("first_limiter")
        if not first_limiter or first_limiter.lower() == "undetermined":
            raise ValueError(
                "capacity_summary.json has undetermined first_limiter; "
                "cannot receive evidenced classification"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write deterministic run manifest")
    parser.add_argument("--run-id", required=True, help="Run ID")
    parser.add_argument("--output-dir", required=True, type=Path, help="Run directory")
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_run_manifest(args.run_id, args.output_dir)

    validate_evidence_integrity(args.output_dir, manifest)

    manifest_path = write_run_manifest(args.output_dir, manifest)
    print(f"Wrote deterministic run manifest to {manifest_path}")

    # Ensure capacity summary classification is refreshed with newly pulled evidence
    cap_path = args.output_dir / "capacity_summary.json"
    if cap_path.is_file():
        cap_data = json.loads(cap_path.read_text(encoding="utf-8"))
        first_lim = cap_data.get("first_limiter", "undetermined")
        ev_paths = {
            "request_results": manifest["evidence"]["request_results"],
            "vllm_metrics": manifest["evidence"]["vllm_metrics"],
            "dcgm_metrics": manifest["evidence"]["dcgm_metrics"],
            "worker_logs": manifest["evidence"]["worker_logs"],
        }
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
        print(f"Verified and updated capacity summary classification: {res.classification}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

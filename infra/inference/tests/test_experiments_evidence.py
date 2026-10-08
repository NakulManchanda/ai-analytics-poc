"""Evidence manifest and integrity checks: hardware must be measured or declared, never assumed."""

from __future__ import annotations

from pathlib import Path

import pytest

from infra.inference.experiments import evidence
from infra.inference.experiments.evidence import build_run_manifest, validate_evidence_integrity

HOST = "name, memory.total [MiB], memory.used [MiB]\nNVIDIA A100-SXM4-40GB, 40960 MiB, 1 MiB\n"
POD = "name, memory.total [MiB], memory.used [MiB]\nNVIDIA A100-SXM4-40GB, 20480 MiB, 1 MiB\n"
VLLM = "# HELP vllm:num_requests_running r\n# TYPE vllm:num_requests_running gauge\n"
DCGM = "# HELP DCGM_FI_DEV_FB_USED fb\n# TYPE DCGM_FI_DEV_FB_USED gauge\nDCGM_FI_DEV_FB_USED 1\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    write(tmp_path / "hardware" / "nvidia-smi.csv", HOST)
    write(tmp_path / "hardware" / "pod-worker-a-nvidia-smi.csv", POD)
    write(tmp_path / "hardware" / "pod-worker-b-nvidia-smi.csv", POD)
    write(tmp_path / "prometheus" / "vllm-worker-a.prom", VLLM)
    write(tmp_path / "prometheus" / "vllm-worker-b.prom", VLLM)
    write(tmp_path / "prometheus" / "dcgm.prom", DCGM)
    write(tmp_path / "logs" / "worker-a.log", "qwen\n")
    write(tmp_path / "logs" / "worker-b.log", "qwen\n")
    write(tmp_path / "raw" / "responses.jsonl", "{}\n")
    return tmp_path


def manifest(run_dir: Path, **kw):
    return build_run_manifest("r1", run_dir, commit_sha="a" * 40, **kw)


def test_measured_hardware_is_recorded(run_dir: Path) -> None:
    hw = manifest(run_dir)["hardware"]
    assert hw["source"] == "nvidia-smi" and hw["gpu"].startswith("NVIDIA")
    assert hw["physical_hbm_bytes"] == 40960 * 1024 * 1024
    assert set(hw["workers"]) == {"inference-worker-a", "inference-worker-b"}


def test_no_hardware_evidence_and_no_declaration_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="hardware evidence missing"):
        build_run_manifest("r1", tmp_path, commit_sha="a" * 40)


def test_declared_hardware_is_labelled(tmp_path: Path) -> None:
    hw = build_run_manifest(
        "r1",
        tmp_path,
        commit_sha="a" * 40,
        gpu_name="NVIDIA A100-SXM4-40GB",
        physical_hbm_bytes=40960 * 1024 * 1024,
    )["hardware"]
    assert hw["source"] == "declared" and "workers" not in hw


def test_declared_non_nvidia_gpu_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an NVIDIA"):
        build_run_manifest(
            "r1", tmp_path, commit_sha="a" * 40, gpu_name="Fake GPU", physical_hbm_bytes=1 << 30
        )


def test_unparseable_host_csv_fails(run_dir: Path) -> None:
    write(run_dir / "hardware" / "nvidia-smi.csv", "garbage\n")
    with pytest.raises(ValueError, match="not a valid nvidia-smi CSV"):
        manifest(run_dir)


def test_missing_pod_csv_fails(run_dir: Path) -> None:
    (run_dir / "hardware" / "pod-worker-b-nvidia-smi.csv").unlink()
    with pytest.raises(ValueError, match="both workers"):
        manifest(run_dir)


def test_pod_slice_not_matching_declared_fraction_fails(run_dir: Path) -> None:
    write(run_dir / "hardware" / "pod-worker-a-nvidia-smi.csv", POD.replace("20480", "40960"))
    with pytest.raises(ValueError, match="worker-a sees"):
        manifest(run_dir)


def test_missing_commit_sha_fails_instead_of_inventing_one(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(evidence.subprocess, "check_output", no_git)
    monkeypatch.delenv("GIT_COMMIT_SHA", raising=False)
    with pytest.raises(ValueError, match="commit SHA unavailable"):
        build_run_manifest("r1", run_dir)


def test_valid_run_passes_integrity(run_dir: Path) -> None:
    validate_evidence_integrity(run_dir, manifest(run_dir))


def test_python_gc_metric_is_not_proof_of_a_vllm_scrape(run_dir: Path) -> None:
    write(
        run_dir / "prometheus" / "vllm-worker-b.prom",
        "# HELP python_gc_objects_collected_total x\n# TYPE python_gc_objects_collected_total c\n",
    )
    with pytest.raises(ValueError, match="vllm:num_requests_running"):
        validate_evidence_integrity(run_dir, manifest(run_dir))


def test_missing_worker_b_scrape_fails(run_dir: Path) -> None:
    (run_dir / "prometheus" / "vllm-worker-b.prom").unlink()
    with pytest.raises(ValueError, match="vllm-worker-b.prom' is missing"):
        validate_evidence_integrity(run_dir, manifest(run_dir))


def test_dcgm_scrape_must_carry_framebuffer_used(run_dir: Path) -> None:
    write(run_dir / "prometheus" / "dcgm.prom", "# HELP DCGM_FI_DEV_GPU_UTIL u\n# TYPE x gauge\n")
    with pytest.raises(ValueError, match="DCGM_FI_DEV_FB_USED"):
        validate_evidence_integrity(run_dir, manifest(run_dir))

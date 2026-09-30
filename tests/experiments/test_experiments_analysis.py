"""Offline tests for experiments/analysis against tiny committed fake run directories."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if (
    str(ROOT) not in sys.path
):  # documented path: `experiments.analysis` from the repo root
    sys.path.insert(0, str(ROOT))

from experiments.analysis import (  # noqa: E402
    PER_REQUEST,
    WINDOW,
    breakdown,
    check_manifests,
    e2_prefix_reuse,
    e3_compare,
    e4_compare,
    jain_index,
    load_run,
    memory_proof,
    sweep_curve,
    ttft_by_turn,
)

FIX = Path(__file__).parent / "fixtures"


def _run(name: str):
    return load_run(FIX / name)


def test_sweep_curve_finds_knee_and_handles_na_token_rates() -> None:
    curve = sweep_curve(_run("sweep_run"))  # csv-only run; level 16 tokens are n/a
    assert curve["knee_metric"] == "good_rps"
    assert curve["knee_offered_concurrency"] == 4
    assert any("n/a" in w for w in curve["warnings"])
    last = curve["rows"][-1]
    assert last["tokens_per_s"] is None and last["completed_rps"] == 4.5
    assert PER_REQUEST in curve["scope"]


def test_sweep_curve_uses_good_tokens_when_all_measured_and_flags_unreached_knee() -> (
    None
):
    run = _run("sweep_run")
    for r in run.sweep:
        r["good_tokens_per_s"] = r["offered_concurrency"] * 10.0
    curve = sweep_curve(run)
    assert curve["knee_metric"] == "good_tokens_per_s"
    assert curve["knee_offered_concurrency"] == 16
    assert any("knee not reached" in w for w in curve["warnings"])


def test_breakdowns_by_class_tenant_worker_and_turn() -> None:
    run = _run("e4_admission_on")
    by_class = breakdown(run, "workload_class")
    assert by_class["scope"] == PER_REQUEST
    assert by_class["rows"]["interactive"]["completed"] == 3
    assert by_class["rows"]["batch"]["requests"] == 9
    assert breakdown(run, "tenant")["rows"]["tenant_noisy"]["requests"] == 6
    e3 = _run("e3_prefix_then_load")
    assert set(breakdown(e3, "worker")["rows"]) == {"worker-a", "worker-b"}
    assert set(ttft_by_turn(e3)["rows"]) == {"turn_1", "turn_2", "turn_3"}
    assert (
        breakdown(e3, "workload_class")["rows"]["interactive"]["queue_wait_ms"]["p95"]
        > 0
    )


def test_e3_compare_reports_deltas_and_matching_manifests() -> None:
    res = e3_compare(_run("e3_least_loaded"), _run("e3_prefix_then_load"))
    assert res["manifest_check"]["match"] is True and res["warnings"] == []
    a, b = res["runs"]["a_least_loaded"], res["runs"]["b_prefix_then_load"]
    assert res["delta_b_minus_a"]["ttft_ms.p50"] < 0
    assert b["placement_reason_by_turn"]["turn_1"] == {"prefix_affinity": 4}
    assert set(a["placement_reason_by_turn"]["turn_1"]) == {"least_loaded"}
    assert sum(b["worker_distribution"].values()) == 12
    assert b["deadline_met_rate"] == 1.0 and b["goodput"]["good_requests_per_s"] == 0.6


def test_manifest_mismatch_and_unverified_controls_are_warned(tmp_path: Path) -> None:
    for name in ("e3_least_loaded", "e3_prefix_then_load"):
        shutil.copytree(FIX / name, tmp_path / name)
    mp = tmp_path / "e3_prefix_then_load" / "manifest.json"
    m = json.loads(mp.read_text())
    m["engine_flags"] = "--max-model-len 4096"
    m["policy_override_verified"] = False
    m["control_unverified_turns"] = 2
    mp.write_text(json.dumps(m))
    res = e3_compare(
        load_run(tmp_path / "e3_least_loaded"),
        load_run(tmp_path / "e3_prefix_then_load"),
    )
    assert res["manifest_check"]["match"] is False
    assert res["manifest_check"]["mismatches"][0]["field"] == "engine_flags"
    text = " ".join(res["warnings"])
    assert (
        "MANIFEST MISMATCH" in text
        and "NOT verified" in text
        and "control_unverified" in text
    )


def test_identical_treatment_is_warned() -> None:
    run = _run("e3_least_loaded")
    check = check_manifests(run, run, varied=("policy_override",))
    assert check["match"] and "not varied" in check["warnings"][0]


def test_e4_compare_sheds_fairness_and_batch_starvation() -> None:
    res = e4_compare(_run("e4_admission_off"), _run("e4_admission_on"))
    off, on = res["runs"]["a_admission_off"], res["runs"]["b_admission_on"]
    assert on["sheds_by_reason"] == {
        "shed:deadline_unreachable": 2,
        "shed:tenant_concurrency": 3,
    }
    assert on["timeout_queue"] == 1 and off["timeout_queue"] == 0
    assert res["delta_b_minus_a"]["goodput.good_requests_per_s"] == 0.2
    assert on["fairness"]["jain_completed_requests"] == pytest.approx(0.857, abs=1e-3)
    assert on["fairness"]["per_tenant"]["tenant_noisy"]["admitted"] == 3
    assert on["batch_starvation"]["batch_starved"] is True
    assert off["batch_starvation"]["batch_starved"] is False
    assert any("on" in w and "starved" in w for w in res["warnings"])
    assert res["manifest_check"]["match"] is True


def test_jain_index() -> None:
    assert jain_index([5, 5, 5]) == 1.0
    assert jain_index([10, 0, 0, 0]) == 0.25
    assert jain_index([]) is None and jain_index([0, 0]) is None


def test_e2_prefix_reuse_labels_window_metrics() -> None:
    res = e2_prefix_reuse(_run("e3_least_loaded"), _run("e3_prefix_then_load"))
    assert (
        res["window"]["scope"] == WINDOW and res["per_request"]["scope"] == PER_REQUEST
    )
    assert res["window"]["hit_rate_pct_delta_reused_minus_cold"] == 45.0
    assert res["per_request"]["reused"]["ttft_ms"]["p50"] is not None
    assert any("never attribute" in w for w in res["warnings"])
    no_window = e2_prefix_reuse(_run("e4_admission_off"), _run("e4_admission_on"))
    assert no_window["window"]["hit_rate_pct_delta_reused_minus_cold"] is None
    assert any("no prometheus_window" in w for w in no_window["warnings"])


def test_memory_proof_joins_series_and_flags_patterns() -> None:
    res = memory_proof(FIX / "prometheus_range")
    assert res["scope"] == WINDOW and len(res["timestamps"]) == 20
    assert {
        "dcgm_fb_used_mib",
        "vllm_kv_cache_usage:worker-a",
        "vllm_requests_running:worker-b",
    } <= set(res["series"])
    assert res["series"]["vllm_prefix_hit_ratio:worker-b"][:3] == [
        None,
        None,
        None,
    ]  # NaN dropped
    flags = {(f["series"], f["flag"]) for f in res["flags"]}
    assert ("vllm_kv_cache_usage:worker-a", "flat_at_max") in flags
    assert ("dcgm_fb_used_mib", "monotonic_growth") in flags
    assert ("vllm_preemption_rate:worker-a", "preemptions") in flags
    assert ("vllm_kv_cache_usage:worker-b", "flat_at_max") not in flags
    assert res["warnings"] == []


def test_memory_proof_on_empty_dir_warns(tmp_path: Path) -> None:
    res = memory_proof(tmp_path)
    assert res["timestamps"] == [] and len(res["warnings"]) == 8


def test_load_run_missing_dir_and_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_run(tmp_path / "nope")
    run = load_run(tmp_path)
    assert run.requests == [] and any("manifest" in w for w in run.warnings)

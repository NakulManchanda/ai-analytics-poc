"""Contract tests for the isolated #120 capacity and evidence tools.

These tests deliberately import production modules inside each test.  The test
file must collect before the tools exist, so the first red run documents the
missing implementation rather than a test-discovery error.
"""

from __future__ import annotations

import importlib
import json

import pytest


def _capacity_module():
    return importlib.import_module("infra.inference.experiments.capacity")


def _evidence_module():
    return importlib.import_module("infra.inference.experiments.evidence")


def _warmup_module():
    return importlib.import_module("infra.inference.experiments.warmup")


def test_kv_bytes_per_token_uses_explicit_attention_dimensions_and_dtype_bytes():
    capacity = _capacity_module()

    # 2 (K and V) * 32 layers * 8 KV heads * 128 head dim * 2 bytes (fp16).
    assert (
        capacity.kv_bytes_per_token(
            num_attention_layers=32,
            num_key_value_heads=8,
            head_dim=128,
            bytes_per_kv_element=2,
        )
        == 131_072
    )


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("num_attention_layers", 0),
        ("num_key_value_heads", 0),
        ("head_dim", 0),
        ("bytes_per_kv_element", 0),
    ],
)
def test_kv_bytes_per_token_rejects_nonpositive_dimensions(keyword: str, value: int):
    capacity = _capacity_module()
    dimensions = {
        "num_attention_layers": 32,
        "num_key_value_heads": 8,
        "head_dim": 128,
        "bytes_per_kv_element": 2,
    }
    dimensions[keyword] = value

    with pytest.raises(ValueError, match=keyword):
        capacity.kv_bytes_per_token(**dimensions)


def test_paper_sequence_ceilings_use_literal_context_lengths_and_byte_budget():
    capacity = _capacity_module()

    # A 512 MiB KV budget and 128 KiB/token give 8 sequences at 512 tokens,
    # 2 at 2,048 tokens, and no whole sequence at 8,192 tokens.
    assert capacity.paper_sequence_ceilings(
        kv_budget_bytes=536_870_912,
        kv_bytes_per_token=131_072,
        context_lengths=(512, 2_048, 8_192),
    ) == {512: 8, 2_048: 2, 8_192: 0}


def test_paper_sequence_ceilings_rejects_nonpositive_budget_or_context():
    capacity = _capacity_module()

    with pytest.raises(ValueError, match="kv_budget_bytes"):
        capacity.paper_sequence_ceilings(
            kv_budget_bytes=0,
            kv_bytes_per_token=131_072,
            context_lengths=(512,),
        )

    with pytest.raises(ValueError, match="context"):
        capacity.paper_sequence_ceilings(
            kv_budget_bytes=536_870_912,
            kv_bytes_per_token=131_072,
            context_lengths=(0,),
        )


def test_run_manifest_is_deterministic_and_contains_explicit_provenance(tmp_path):
    evidence = _evidence_module()
    manifest = {
        "run_id": "capacity-20260927-0001",
        "timestamp_utc": "2026-09-27T14:30:00Z",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "model": {
            "name": "Qwen/Qwen3-0.6B",
            "revision": "c1899de289a04d12100db370d81485cdf75e47ca",
            "tokenizer": "Qwen/Qwen3-0.6B",
        },
        "runtime": {
            "image": "vllm/vllm-openai:v0.11.0",
            "vllm_version": "0.11.0",
            "hami_version": "2.9.0",
            "flags": {"max_model_len": 8192, "max_num_seqs": 8},
        },
        "hardware": {
            "gpu": "NVIDIA A10",
            "physical_hbm_bytes": 25_769_803_776,
            "pod_visible_hbm_bytes": 12_884_901_888,
        },
        "workload": {"seed": 120, "concurrency": 2, "context_lengths": [512, 2048]},
        "evidence": {
            "request_results": "raw/responses.jsonl",
            "vllm_metrics": "metrics/vllm.prom",
            "dcgm_metrics": "metrics/dcgm.prom",
            "worker_logs": "logs/worker-a.log",
        },
    }

    first_path = evidence.write_run_manifest(tmp_path, manifest)
    first_contents = first_path.read_bytes()
    second_path = evidence.write_run_manifest(tmp_path, manifest)

    assert first_path == tmp_path / "run-manifest.json"
    assert second_path == first_path
    assert second_path.read_bytes() == first_contents
    assert json.loads(first_contents) == manifest


def test_run_manifest_refuses_missing_provenance_before_writing(tmp_path):
    evidence = _evidence_module()
    incomplete_manifest = {
        "run_id": "capacity-20260927-0001",
        "timestamp_utc": "2026-09-27T14:30:00Z",
        # An exact immutable model revision is required; a model name alone is
        # not enough to reconcile a result later.
        "model": {"name": "Qwen/Qwen3-0.6B", "tokenizer": "Qwen/Qwen3-0.6B"},
        "runtime": {"image": "vllm/vllm-openai:v0.11.0", "flags": {}},
        "hardware": {"gpu": "NVIDIA A10", "pod_visible_hbm_bytes": 1},
        "workload": {"seed": 120, "concurrency": 1, "context_lengths": [512]},
        "evidence": {"request_results": "raw/responses.jsonl"},
    }

    with pytest.raises(ValueError, match="commit_sha|revision|provenance"):
        evidence.write_run_manifest(tmp_path, incomplete_manifest)

    assert not (tmp_path / "run-manifest.json").exists()


def test_warmup_aggregation_preserves_first_cold_ttft_and_nearest_rank_warm_percentiles():
    warmup = _warmup_module()

    summary = warmup.aggregate_warmup_ttft((900, 100, 120, 140, 160, 180))

    assert summary.cold_ttft_ms == 900
    assert summary.warm_sample_count == 5
    # Sorted warm samples are 100, 120, 140, 160, 180.  Nearest-rank p50 is
    # the third sample and p95 is the fifth sample.
    assert summary.warm_ttft_p50_ms == 140
    assert summary.warm_ttft_p95_ms == 180


def test_warmup_aggregation_requires_a_cold_sample_and_at_least_one_warm_sample():
    warmup = _warmup_module()

    with pytest.raises(ValueError, match="cold|warm|two"):
        warmup.aggregate_warmup_ttft((900,))


def test_capacity_classification_refuses_a_guessed_limiter_without_required_evidence():
    capacity = _capacity_module()

    with pytest.raises(ValueError, match="evidence"):
        capacity.classify_capacity_result(
            first_limiter="kv_cache",
            evidence_paths={"request_results": "raw/responses.jsonl"},
        )


def test_capacity_classification_marks_an_observed_limiter_as_evidenced():
    capacity = _capacity_module()
    evidence_paths = {
        "request_results": "raw/responses.jsonl",
        "vllm_metrics": "metrics/vllm.prom",
        "dcgm_metrics": "metrics/dcgm.prom",
        "worker_logs": "logs/worker-a.log",
    }

    result = capacity.classify_capacity_result(
        first_limiter="kv_cache",
        evidence_paths=evidence_paths,
    )

    assert result.first_limiter == "kv_cache"
    assert result.classification == "evidenced"
    assert result.evidence_paths == evidence_paths

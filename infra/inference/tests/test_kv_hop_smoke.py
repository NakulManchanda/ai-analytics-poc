from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.inference.mooncake.smoke import (
    E5_PREFIX_SIZES,
    _json_events,
    _nominal_prompt_tokens,
    _prefix_hits,
    _prompt,
    _target_event,
    _ttft_ms,
)


def _event(**updates):
    value = {
        "event": "kv_hop",
        "request_id": "request-1",
        "destination_worker": "worker_a",
        "reusable_tokens": 256,
        "transferred_tokens": 0,
        "transferred_bytes": 0,
        "destination_consumed": False,
        "consumed": False,
    }
    value.update(updates)
    return value


def test_parses_structured_events_from_prefixed_worker_logs() -> None:
    log = 'INFO worker message {"event":"kv_hop","request_id":"request-1"}\nnoise\n'
    assert _json_events(log) == [{"event": "kv_hop", "request_id": "request-1"}]


def test_prefix_hit_counter_accepts_vllm_counter_spellings() -> None:
    metrics = """
vllm:prefix_cache_hits_total{model_name="m"} 12
vllm:gpu_prefix_cache_hits{model_name="m"} 3
vllm:prefix_cache_queries_total 20
"""
    assert _prefix_hits(metrics) == 15


def test_local_proof_requires_metric_delta_and_successful_response() -> None:
    response = {"status": 200}
    event = _target_event(
        "same_worker_local_reuse",
        "request-1",
        [_event()],
        response,
        256,
    )
    assert event["consumed"] is True
    assert event["source_worker_or_store"] == "worker_a"
    assert event["evidence_scope"] == "isolated_window"

    with pytest.raises(RuntimeError, match="no isolated local-prefix hit evidence"):
        _target_event(
            "same_worker_local_reuse",
            "request-1",
            [_event()],
            response,
            0,
        )


def test_real_transfer_never_promotes_availability_to_consumption() -> None:
    available = _event(
        event="kv_hop_available",
        destination_worker="worker_b",
        transferred_tokens=256,
        transferred_bytes=4096,
    )
    with pytest.raises(RuntimeError, match="no final KV event"):
        _target_event(
            "real_mooncake_transfer_consumed",
            "request-1",
            [available],
            {"status": 200},
            0,
        )


def test_real_transfer_preserves_worker_confirmed_consumption() -> None:
    transferred = _event(
        destination_worker="worker_b",
        transferred_tokens=256,
        transferred_bytes=4096,
        destination_consumed=True,
        consumed=True,
    )
    event = _target_event(
        "real_mooncake_transfer_consumed",
        "request-1",
        [transferred],
        {"status": 200},
        0,
    )
    assert event["consumed"] is True


def test_example_evidence_inputs_cover_required_version_fields() -> None:
    root = Path(__file__).resolve().parents[1] / "mooncake"
    topology = json.loads((root / "topology.example.json").read_text())
    versions = json.loads((root / "versions.example.json").read_text())

    assert topology["hami_scheduler"] is True
    assert topology["worker_a"]["gpu_core_percentage"] == 50
    assert topology["worker_b"]["gpu_core_percentage"] == 50
    assert versions["engine_version"] == "0.11.0"
    assert versions["prefix_identity_version"] == "kv-prefix-identity-v1"
    assert versions["model_revision"] == versions["tokenizer_revision"]


def test_prompt_scales_to_e5_prefix_sizes() -> None:
    p1k = _prompt("run-1", "case", "1k")
    p2k = _prompt("run-1", "case", "2k")
    p4k = _prompt("run-1", "case", "4k")
    p7k = _prompt("run-1", "case", "7k")

    assert len(p1k) < len(p2k) < len(p4k) < len(p7k)
    assert set(E5_PREFIX_SIZES.keys()) == {"1k", "2k", "4k", "7k"}
    for size, target in E5_PREFIX_SIZES.items():
        prompt = _prompt("run-1", "case", size)
        tokens = _nominal_prompt_tokens(prompt)
        assert abs(tokens - target) <= 15
        assert len(prompt) / target >= 6.0


def test_ttft_ms_derives_latency_from_prometheus_windows() -> None:
    before = """
vllm:time_to_first_token_seconds_count{model="m"} 10
vllm:time_to_first_token_seconds_sum{model="m"} 1.50
"""
    after = """
vllm:time_to_first_token_seconds_count{model="m"} 11
vllm:time_to_first_token_seconds_sum{model="m"} 1.75
"""
    # 0.25 seconds / 1 request = 250.0 ms
    assert _ttft_ms(before, after) == 250.0

    # No count advance returns None
    assert _ttft_ms(before, before) is None

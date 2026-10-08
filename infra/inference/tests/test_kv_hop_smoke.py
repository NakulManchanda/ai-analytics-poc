import argparse
import json
from pathlib import Path

import pytest

from infra.inference.mooncake import smoke
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
    assert topology["workers"] == ["inference-worker-a", "inference-worker-b"]
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

    # Count advance > 1 returns None to fail closed on multi-request windows
    multi = """
vllm:time_to_first_token_seconds_count{model="m"} 12
vllm:time_to_first_token_seconds_sum{model="m"} 2.00
"""
    assert _ttft_ms(before, multi) is None


def test_smoke_run_multi_size_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    topology_file = tmp_path / "topology.json"
    topology_file.write_text(
        json.dumps({
            "workers": ["worker_a", "worker_b"],
            "worker_a": {"gpu_core_percentage": 50},
            "worker_b": {"gpu_core_percentage": 50},
        })
    )
    versions_file = tmp_path / "versions.json"
    versions_data = {
        "model_id": "Qwen/Qwen3-0.6B",
        "model_revision": "rev-1",
        "tokenizer_revision": "rev-1",
        "chat_template_version": "v1",
        "prefix_contract_version": "v1",
        "weight_dtype": "bfloat16",
        "kv_dtype": "auto",
        "adapter_namespace": "none",
        "cache_namespace": "test-ns",
        "engine_version": "0.11.0",
        "block_layout_version": "v1",
        "prefix_identity_version": "kv-prefix-identity-v1",
    }
    versions_file.write_text(json.dumps(versions_data))
    from infra.inference.kv_transfer.identity import CompatibilitySpec

    compat_ns = CompatibilitySpec(
        **{k: versions_data[k] for k in CompatibilitySpec.__dataclass_fields__}
    ).namespace
    out_dir = tmp_path / "smoke_out"

    args = argparse.Namespace(
        output_dir=str(out_dir),
        topology_file=str(topology_file),
        versions_file=str(versions_file),
        worker_a_url="http://worker-a:8000",
        worker_b_url="http://worker-b:8000",
        gateway_url="http://gateway:8000",
        namespace="inference-lab",
        prefix_sizes=["1k", "2k"],
        run_id="test-run",
    )

    metrics_calls = 0

    def fake_get(url: str) -> str:
        nonlocal metrics_calls
        if url.endswith("/health"):
            return json.dumps({"status": "ok"})
        if url.endswith("/metrics"):
            metrics_calls += 1
            return f"""
vllm:prefix_cache_hits_total {metrics_calls * 10}
vllm:time_to_first_token_seconds_count {metrics_calls}
vllm:time_to_first_token_seconds_sum {metrics_calls * 0.25}
"""
        raise ValueError(f"unexpected get {url}")

    recorded_requests: list[tuple[str, str]] = []

    def fake_post(url: str, body: dict, headers: dict) -> dict:
        req_id = headers["x-request-id"]
        case = headers["x-kv-hop-case"]
        recorded_requests.append((req_id, case))
        return {
            "status": 200,
            "headers": {},
            "body": {"choices": [{"message": {"content": "OK"}}]},
        }

    def fake_kubectl(namespace: str, *k_args: str) -> str:
        if k_args[0] == "get":
            return json.dumps({"items": []})
        if k_args[0] == "logs":
            lines = []
            for req_id, case in recorded_requests:
                if case == "same_worker_local_reuse":
                    lines.append(
                        json.dumps({
                            "event": "kv_hop",
                            "request_id": req_id,
                            "conversation_id": "c1",
                            "agent_step": 1,
                            "source_worker_or_store": "worker_a",
                            "destination_worker": "worker_a",
                            "compatibility_namespace": compat_ns,
                            "prefix_identity": "p1",
                            "prefix_version": "kv-prefix-identity-v1",
                            "hop_decision_reason": "local_prefix_present",
                            "hop_result": "local_reuse",
                            "reusable_tokens": 1024,
                            "transferred_tokens": 0,
                            "transferred_bytes": 0,
                            "lookup_ms": 0.2,
                            "transfer_ms": 0.0,
                            "confirm_ms": 0.1,
                            "fallback_action": "none",
                            "consumed": True,
                            "destination_consumed": True,
                            "evidence_scope": "isolated_window",
                        })
                    )
                elif case == "cross_worker_recompute_transfer_disabled":
                    lines.append(
                        json.dumps({
                            "event": "kv_hop",
                            "request_id": req_id,
                            "conversation_id": "c1",
                            "agent_step": 1,
                            "source_worker_or_store": "worker_a",
                            "destination_worker": "worker_b",
                            "compatibility_namespace": compat_ns,
                            "prefix_identity": "p1",
                            "prefix_version": "kv-prefix-identity-v1",
                            "hop_decision_reason": "transfer_disabled",
                            "hop_result": "recomputed",
                            "reusable_tokens": 0,
                            "transferred_tokens": 0,
                            "transferred_bytes": 0,
                            "lookup_ms": 0.2,
                            "transfer_ms": 0.0,
                            "confirm_ms": 0.1,
                            "fallback_action": "recompute",
                            "consumed": False,
                            "destination_consumed": False,
                            "evidence_scope": "per_request",
                        })
                    )
                elif case == "independently_warmed_destination_local_hit":
                    lines.append(
                        json.dumps({
                            "event": "kv_hop",
                            "request_id": req_id,
                            "conversation_id": "c1",
                            "agent_step": 1,
                            "source_worker_or_store": "worker_b",
                            "destination_worker": "worker_b",
                            "compatibility_namespace": compat_ns,
                            "prefix_identity": "p1",
                            "prefix_version": "kv-prefix-identity-v1",
                            "hop_decision_reason": "independently_warmed_destination",
                            "hop_result": "destination_hit",
                            "reusable_tokens": 1024,
                            "transferred_tokens": 0,
                            "transferred_bytes": 0,
                            "lookup_ms": 0.2,
                            "transfer_ms": 0.0,
                            "confirm_ms": 0.1,
                            "fallback_action": "none",
                            "consumed": True,
                            "destination_consumed": True,
                            "evidence_scope": "isolated_window",
                        })
                    )
                elif case == "real_mooncake_transfer_consumed":
                    lines.append(
                        json.dumps({
                            "event": "kv_hop",
                            "request_id": req_id,
                            "conversation_id": "c1",
                            "agent_step": 1,
                            "source_worker_or_store": "mooncake_store",
                            "destination_worker": "worker_b",
                            "compatibility_namespace": compat_ns,
                            "prefix_identity": "p1",
                            "prefix_version": "kv-prefix-identity-v1",
                            "hop_decision_reason": "remote_prefix_available",
                            "hop_result": "transferred",
                            "reusable_tokens": 1024,
                            "transferred_tokens": 1024,
                            "transferred_bytes": 524288,
                            "lookup_ms": 0.4,
                            "transfer_ms": 5.1,
                            "confirm_ms": 0.2,
                            "fallback_action": "none",
                            "destination_consumed": True,
                            "consumed": True,
                            "evidence_scope": "per_request",
                        })
                    )
            return "\n".join(lines) + "\n"
        raise ValueError(f"unexpected kubectl {k_args}")

    monkeypatch.setattr(smoke, "_get", fake_get)
    monkeypatch.setattr(smoke, "_post", fake_post)
    monkeypatch.setattr(smoke, "_kubectl", fake_kubectl)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)

    validation = smoke.run(args)
    assert validation["valid"] is True
    assert validation["case_count"] == 8

    crossover = json.loads((out_dir / "crossover.json").read_text())
    assert set(crossover.keys()) == {"1k", "2k"}
    for size in ("1k", "2k"):
        entry = crossover[size]
        assert entry["actual_reusable_tokens"] == 1024
        assert entry["transferred_tokens"] == 1024
        assert entry["transferred_bytes"] == 524288
        assert entry["transfer_ms"] == 5.1
        assert entry["destination_consumed"] is True
        assert entry["recompute_e2e_ms"] is not None
        assert entry["transfer_e2e_ms"] is not None
        assert entry["recompute_ttft_ms"] == 250.0
        assert entry["transfer_ttft_ms"] == 250.0

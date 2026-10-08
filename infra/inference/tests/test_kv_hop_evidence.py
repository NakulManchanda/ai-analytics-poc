from __future__ import annotations

from copy import deepcopy

import pytest

from infra.inference.kv_transfer.evidence import validate_case, validate_run

CASE_NAMES = (
    "same_worker_local_reuse",
    "cross_worker_recompute_transfer_disabled",
    "independently_warmed_destination_local_hit",
    "real_mooncake_transfer_consumed",
)


def _event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "request_id": "request-1",
        "conversation_id": "conversation-1",
        "agent_step": 2,
        "prefix_identity": "sha256:canonical-prefix",
        "prefix_version": "kv-prefix-identity-v1",
        "source_worker_or_store": "worker-a",
        "destination_worker": "worker-a",
        "compatibility_namespace": "kv-compat-v1:sha256:namespace",
        "hop_decision_reason": "local_prefix_present",
        "hop_result": "local_reuse",
        "reusable_tokens": 256,
        "transferred_tokens": 0,
        "transferred_bytes": 0,
        "lookup_ms": 0.4,
        "transfer_ms": 0.0,
        "confirm_ms": 0.2,
        "fallback_action": "none",
        "consumed": True,
        "evidence_scope": "per_request",
    }
    event.update(overrides)
    return event


def _case(name: str, event: dict[str, object]) -> dict[str, object]:
    return {"name": name, "events": [event]}


def _four_cases() -> list[dict[str, object]]:
    return [
        _case("same_worker_local_reuse", _event()),
        _case(
            "cross_worker_recompute_transfer_disabled",
            _event(
                destination_worker="worker-b",
                hop_decision_reason="transfer_disabled",
                hop_result="recomputed",
                reusable_tokens=0,
                consumed=False,
                fallback_action="recompute",
            ),
        ),
        _case(
            "independently_warmed_destination_local_hit",
            _event(
                source_worker_or_store="worker-b",
                destination_worker="worker-b",
                hop_decision_reason="independently_warmed_destination",
                hop_result="destination_hit",
            ),
        ),
        _case(
            "real_mooncake_transfer_consumed",
            _event(
                source_worker_or_store="mooncake_store",
                destination_worker="worker-b",
                hop_decision_reason="remote_prefix_available",
                hop_result="transferred",
                transferred_tokens=256,
                transferred_bytes=262_144,
                transfer_ms=7.2,
            ),
        ),
    ]


def _manifest() -> dict[str, object]:
    return {
        "topology": {
            "workers": ["worker-a", "worker-b"],
            "stores": ["mooncake_store"],
            "gpu": "one A100 with two HAMi slices",
        },
        "versions": {
            "model_id": "Qwen/Qwen3-0.6B",
            "model_revision": "model-sha",
            "tokenizer_revision": "tokenizer-sha",
            "chat_template_version": "qwen3-v1",
            "prefix_contract_version": "prefix-v1",
            "weight_dtype": "float16",
            "kv_dtype": "float16",
            "adapter_namespace": "none",
            "cache_namespace": "issue-133-isolated",
            "engine_version": "vllm-pinned",
            "block_layout_version": "v1",
            "prefix_identity_version": "kv-prefix-identity-v1",
        },
        "compatibility_namespace": "kv-compat-v1:sha256:namespace",
        "control_isolation_statement": (
            "Each arm uses a fresh worker generation and an isolated metrics window."
        ),
    }


@pytest.mark.parametrize("case", _four_cases(), ids=CASE_NAMES)
def test_validate_case_accepts_each_controlled_case(case: dict[str, object]) -> None:
    summary = validate_case(case, case["events"])

    assert summary["case"] == case["name"]
    assert summary["event_count"] == 1


def test_validate_run_checks_all_four_cases() -> None:
    summary = validate_run(_manifest(), _four_cases())

    assert summary == {
        "valid": True,
        "case_count": 4,
        "cases": list(CASE_NAMES),
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"transferred_bytes": 0}, "positive transferred bytes"),
        ({"transferred_tokens": 0}, "positive transferred tokens"),
        ({"consumed": False}, "destination consumption"),
        ({"hop_result": "partial"}, "transferred result"),
        (
            {"source_worker_or_store": "worker-b"},
            "source and destination must differ",
        ),
    ],
)
def test_real_hop_rejects_ghost_zero_or_partial_evidence(
    changes: dict[str, object], message: str
) -> None:
    case = _four_cases()[-1]
    event = deepcopy(case["events"])[0]
    event.update(changes)

    with pytest.raises(ValueError, match=message):
        validate_case(case, [event])


def test_independent_destination_hit_is_not_accepted_as_real_hop() -> None:
    independent_hit = deepcopy(_four_cases()[2]["events"])

    with pytest.raises(ValueError, match="transferred result"):
        validate_case({"name": "real_mooncake_transfer_consumed"}, independent_hit)


def test_independently_warmed_hit_accepts_runtime_local_reuse_result() -> None:
    event = deepcopy(_four_cases()[2]["events"])[0]
    event["hop_result"] = "local_reuse"

    summary = validate_case({"name": "independently_warmed_destination_local_hit"}, [event])

    assert summary["case"] == "independently_warmed_destination_local_hit"


def test_runtime_prefix_identity_version_alias_is_accepted() -> None:
    event = deepcopy(_four_cases()[-1]["events"])[0]
    event["prefix_identity_version"] = event.pop("prefix_version")

    summary = validate_case({"name": "real_mooncake_transfer_consumed"}, [event])

    assert summary["case"] == "real_mooncake_transfer_consumed"


def test_latency_and_headers_do_not_substitute_for_transfer_evidence() -> None:
    event = deepcopy(_four_cases()[-1]["events"])[0]
    event.update(
        {
            "transferred_tokens": 0,
            "transferred_bytes": 0,
            "transfer_ms": 0.1,
            "worker_header": "worker-b",
            "ttft_ms": 1.0,
        }
    )

    with pytest.raises(ValueError, match="positive transferred tokens"):
        validate_case({"name": "real_mooncake_transfer_consumed"}, [event])


def test_incomplete_event_has_a_bounded_failure_reason() -> None:
    event = deepcopy(_four_cases()[-1]["events"])[0]
    del event["fallback_action"]
    event["request_id"] = "x" * 10_000

    with pytest.raises(ValueError) as caught:
        validate_case({"name": "real_mooncake_transfer_consumed"}, [event])

    assert "missing event fields" in str(caught.value)
    assert len(str(caught.value)) < 200


def test_correlated_events_must_identify_the_same_request_and_prefix() -> None:
    events = deepcopy(_four_cases()[-1]["events"])
    second = deepcopy(events[0])
    second["request_id"] = "different-request"
    events.append(second)

    with pytest.raises(ValueError, match="correlation fields do not match"):
        validate_case({"name": "real_mooncake_transfer_consumed"}, events)


@pytest.mark.parametrize("missing", ["topology", "versions", "compatibility_namespace"])
def test_run_rejects_missing_topology_or_version_namespace_inputs(missing: str) -> None:
    manifest = _manifest()
    del manifest[missing]

    with pytest.raises(ValueError, match="manifest"):
        validate_run(manifest, _four_cases())


def test_run_rejects_missing_control_isolation_statement() -> None:
    manifest = _manifest()
    manifest["control_isolation_statement"] = ""

    with pytest.raises(ValueError, match="control isolation"):
        validate_run(manifest, _four_cases())


def test_run_rejects_namespace_and_prefix_version_mismatch() -> None:
    namespace_cases = _four_cases()
    namespace_cases[-1]["events"][0]["compatibility_namespace"] = "other"
    with pytest.raises(ValueError, match="compatibility namespace"):
        validate_run(_manifest(), namespace_cases)

    version_cases = _four_cases()
    version_cases[-1]["events"][0]["prefix_version"] = "kv-prefix-identity-v2"
    with pytest.raises(ValueError, match="prefix identity version"):
        validate_run(_manifest(), version_cases)


def test_run_rejects_missing_or_duplicate_control_case() -> None:
    cases = _four_cases()
    with pytest.raises(ValueError, match="four controlled cases"):
        validate_run(_manifest(), cases[:-1])

    cases[-1]["name"] = "same_worker_local_reuse"
    with pytest.raises(ValueError, match="duplicate case"):
        validate_run(_manifest(), cases)

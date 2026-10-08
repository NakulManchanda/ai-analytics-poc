from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from infra.inference.kv_transfer.identity import CompatibilitySpec, PrefixIdentity
from infra.inference.kv_transfer.runtime import (
    HOP_ACTION_KEY,
    HOP_NAMESPACE_KEY,
    HOP_PREFIX_ID_KEY,
    LMCACHE_COMPATIBILITY_TAG,
    KVHopRuntime,
    KVTransferIntegrityError,
    compatibility_from_vllm,
    validate_lmcache_runtime,
)


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision="model-commit",
        tokenizer_revision="tokenizer-commit",
        chat_template_version="chat-v1",
        prefix_contract_version="prefix-v1",
        weight_dtype="bfloat16",
        kv_dtype="bfloat16",
        adapter_namespace="none",
        cache_namespace="taxi-e5",
        engine_version="0.11.0",
        block_layout_version="vllm-v1:block-size=16:tp=1",
    )


def _request(*, load: bool, params: dict | None = None):
    hop = {
        "lmcache.hop.load": load,
        "lmcache.hop.request_id": "req-7",
        "lmcache.hop.conversation_id": "conv-3",
        "lmcache.hop.agent_step": 2,
        "lmcache.hop.reason": "cross_worker_shared_store",
        "lmcache.hop.deadline_epoch_s": 2_000_000_000.0,
    }
    hop.update(params or {})
    return SimpleNamespace(
        request_id="req-7",
        prompt_token_ids=[11, 12, 13, 14],
        sampling_params=SimpleNamespace(extra_args={"kv_transfer_params": hop}),
    )


def test_disabled_runtime_skips_lookup_without_removing_save_metadata() -> None:
    events: list[dict] = []
    runtime = KVHopRuntime(
        enabled=False,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        emit=events.append,
    )
    request = _request(load=True)

    def upstream(_request, _computed):
        raise AssertionError("disabled runtime must not query external KV")

    assert runtime.get_num_new_matched_tokens(request, 0, upstream) == (0, False)
    assert request.sampling_params.extra_args["kv_transfer_params"]["lmcache.hop.load"] is True
    assert events[0]["hop_result"] == "recomputed"
    assert events[0]["fallback_action"] == "recompute"
    assert events[0]["destination_consumed"] is False


def test_no_transfer_records_observed_local_reuse_separately() -> None:
    events: list[dict] = []
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        emit=events.append,
    )
    request = _request(load=False)

    assert runtime.get_num_new_matched_tokens(
        request,
        4,
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not lookup")),
    ) == (0, False)
    assert events[0]["hop_result"] == "local_reuse"
    assert events[0]["reusable_tokens"] == 4
    assert events[0]["transferred_tokens"] == 0
    assert events[0]["transferred_bytes"] == 0
    assert events[0]["confirm_result"] == "available"
    assert events[0]["destination_reused_tokens"] == 4
    assert events[0]["destination_consumed"] is False
    assert events[0]["consumed"] is False


def test_request_without_explicit_load_policy_skips_external_lookup() -> None:
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
    )
    request = _request(load=False)

    def upstream(_request, _computed):
        raise AssertionError("recompute policy must not query external KV")

    assert runtime.get_num_new_matched_tokens(request, 0, upstream) == (0, False)


def test_lookup_injects_worker_owned_identity_and_records_lookup_timing() -> None:
    events: list[dict] = []
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        emit=events.append,
        clock=_Clock([10.0, 10.025]),
    )
    request = _request(load=True)

    result = runtime.get_num_new_matched_tokens(
        request,
        0,
        lambda _request, _computed: (4, False),
    )

    expected = PrefixIdentity.build([11, 12, 13, 14], _compatibility())
    params = request.sampling_params.extra_args["kv_transfer_params"]
    assert result == (4, False)
    assert params[LMCACHE_COMPATIBILITY_TAG] == _compatibility().namespace
    assert params[HOP_NAMESPACE_KEY] == _compatibility().namespace
    assert params[HOP_PREFIX_ID_KEY] == expected.value
    assert params[HOP_ACTION_KEY] == "load"
    assert params["lmcache.hop.lookup_ms"] == pytest.approx(25.0)
    assert params["lmcache.hop.reusable_tokens"] == 4
    assert events == []


@pytest.mark.parametrize(
    ("key", "untrusted"),
    [
        (LMCACHE_COMPATIBILITY_TAG, "kv-compat-v1:sha256:wrong"),
        (HOP_NAMESPACE_KEY, "kv-compat-v1:sha256:wrong"),
        (HOP_PREFIX_ID_KEY, "sha256:wrong"),
    ],
)
def test_lookup_rejects_client_identity_that_disagrees_with_worker(
    key: str, untrusted: str
) -> None:
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
    )
    request = _request(load=True, params={key: untrusted})

    with pytest.raises(KVTransferIntegrityError, match="worker-derived"):
        runtime.get_num_new_matched_tokens(
            request,
            0,
            lambda _request, _computed: (4, False),
        )


def test_real_retrieval_emits_confirmed_consumption_after_gpu_synchronize() -> None:
    events: list[dict] = []
    sync_order: list[str] = []
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        source_worker_or_store="mooncake_store",
        emit=events.append,
        clock=_Clock([20.0, 20.010, 20.014, 20.030]),
        synchronize=lambda: sync_order.append("synchronized"),
    )
    engine = _FakeEngine(retrieved=4, transferred_bytes=4096)
    runtime.instrument_engine(engine)

    mask = engine.retrieve(
        [11, 12, 13, 14],
        _Mask(4),
        req_id="req-7",
        request_configs=_request(load=True).sampling_params.extra_args["kv_transfer_params"]
        | _identity_params(),
    )

    assert mask.sum().item() == 4
    assert sync_order == ["synchronized"]
    assert events[0]["event"] == "kv_hop_available"
    assert events[0]["destination_consumed"] is False

    runtime.confirm_forward_consumption()

    assert len(events) == 2
    event = events[1]
    assert event["event"] == "kv_hop"
    assert event["request_id"] == "req-7"
    assert event["source_worker_or_store"] == "mooncake_store"
    assert event["destination_worker"] == "worker_b"
    assert event["transferred_tokens"] == 4
    assert event["transferred_bytes"] == 4096
    assert event["transfer_ms"] == pytest.approx(10.0)
    assert event["confirm_ms"] == pytest.approx(20.0)
    assert event["hop_result"] == "transferred"
    assert event["confirm_result"] == "available"
    assert event["destination_reused_tokens"] == 4
    assert event["destination_consumed"] is True
    assert event["consumed"] is True


def test_partial_retrieval_fails_closed_and_never_claims_consumption() -> None:
    events: list[dict] = []
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        emit=events.append,
        clock=_Clock([30.0, 30.010]),
        synchronize=lambda: None,
    )
    engine = _FakeEngine(retrieved=2, transferred_bytes=2048)
    runtime.instrument_engine(engine)

    with pytest.raises(KVTransferIntegrityError, match="partial KV retrieval"):
        engine.retrieve(
            [11, 12, 13, 14],
            _Mask(4),
            req_id="req-7",
            request_configs=_request(load=True).sampling_params.extra_args["kv_transfer_params"]
            | _identity_params(),
        )

    assert events[0]["hop_result"] == "failed"
    assert events[0]["confirm_result"] == "missing"
    assert events[0]["destination_consumed"] is False
    assert events[0]["transferred_tokens"] == 2


def test_lmcache_runtime_rejects_unsafe_modes_at_startup() -> None:
    with pytest.raises(RuntimeError, match="tensor_parallel_size=1"):
        validate_lmcache_runtime(
            tensor_parallel_size=2,
            use_layerwise=False,
            enable_async_loading=False,
            remote_url="mooncakestore://mooncake-master:50051/",
            local_cpu=False,
        )
    with pytest.raises(RuntimeError, match="layerwise"):
        validate_lmcache_runtime(
            tensor_parallel_size=1,
            use_layerwise=True,
            enable_async_loading=False,
            remote_url="mooncakestore://mooncake-master:50051/",
            local_cpu=False,
        )
    with pytest.raises(RuntimeError, match="async"):
        validate_lmcache_runtime(
            tensor_parallel_size=1,
            use_layerwise=False,
            enable_async_loading=True,
            remote_url="mooncakestore://mooncake-master:50051/",
            local_cpu=False,
        )
    with pytest.raises(RuntimeError, match="Mooncake Store"):
        validate_lmcache_runtime(
            tensor_parallel_size=1,
            use_layerwise=False,
            enable_async_loading=False,
            remote_url="redis://cache:6379/",
            local_cpu=False,
        )
    with pytest.raises(RuntimeError, match="local_cpu"):
        validate_lmcache_runtime(
            tensor_parallel_size=1,
            use_layerwise=False,
            enable_async_loading=False,
            remote_url="mooncakestore://mooncake-master:50051/",
            local_cpu=True,
        )


def test_evidence_is_json_serializable_without_raw_tokens() -> None:
    events: list[dict] = []
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        emit=events.append,
        clock=_Clock([40.0, 40.001, 40.002, 40.003]),
        synchronize=lambda: None,
    )
    engine = _FakeEngine(retrieved=4, transferred_bytes=4096)
    runtime.instrument_engine(engine)
    engine.retrieve(
        [11, 12, 13, 14],
        _Mask(4),
        req_id="req-7",
        request_configs=_request(load=True).sampling_params.extra_args["kv_transfer_params"]
        | _identity_params(),
    )
    runtime.confirm_forward_consumption()

    encoded = json.dumps(events[-1], sort_keys=True)
    assert "11" not in encoded
    assert "rendered_token_ids" not in encoded


def test_compatibility_uses_renderer_env_and_resolved_engine_layout() -> None:
    config = SimpleNamespace(
        model_config=SimpleNamespace(
            model="Qwen/Qwen2.5-7B-Instruct",
            revision="model-commit",
            tokenizer_revision=None,
            dtype="bfloat16",
        ),
        cache_config=SimpleNamespace(cache_dtype="auto", block_size=16),
        parallel_config=SimpleNamespace(tensor_parallel_size=1),
        lora_config=None,
    )
    versions = {"vllm": "0.11.0", "lmcache": "0.3.9"}

    compatibility = compatibility_from_vllm(
        config,
        {
            "KV_TOKENIZER_REVISION": "tokenizer-commit",
            "KV_TEMPLATE_VERSION": "chat-v1",
            "KV_PREFIX_CONTRACT_VERSION": "prefix-v1",
            "KV_CACHE_NAMESPACE": "taxi-e5",
        },
        package_version=versions.__getitem__,
    )

    assert compatibility.tokenizer_revision == "tokenizer-commit"
    assert compatibility.chat_template_version == "chat-v1"
    assert compatibility.prefix_contract_version == "prefix-v1"
    assert compatibility.kv_dtype == "bfloat16"
    assert compatibility.adapter_namespace == "none"
    assert compatibility.block_layout_version == "vllm-v1:block-size=16:tp=1"


def _identity_params() -> dict:
    identity = PrefixIdentity.build([11, 12, 13, 14], _compatibility())
    return {
        LMCACHE_COMPATIBILITY_TAG: identity.compatibility_namespace,
        HOP_NAMESPACE_KEY: identity.compatibility_namespace,
        HOP_PREFIX_ID_KEY: identity.value,
        "lmcache.hop.prefix_identity_version": identity.version,
        "lmcache.hop.lookup_ms": 3.5,
        "lmcache.hop.reusable_tokens": 4,
    }


def test_retrieval_with_elapsed_deadline_proceeds_without_aborting_batch() -> None:
    events: list[dict] = []
    runtime = KVHopRuntime(
        enabled=True,
        compatibility=_compatibility(),
        destination_worker="worker_b",
        emit=events.append,
        wall_clock=lambda: 2_000_000_001.0,
        synchronize=lambda: None,
    )
    engine = _FakeEngine(retrieved=4, transferred_bytes=4096)
    runtime.instrument_engine(engine)

    # deadline_epoch_s in _request is 2_000_000_000.0, earlier than wall_clock
    mask = engine.retrieve(
        [11, 12, 13, 14],
        _Mask(4),
        req_id="req-7",
        request_configs=_request(load=True).sampling_params.extra_args["kv_transfer_params"]
        | _identity_params(),
    )
    assert mask.sum().item() == 4
    runtime.confirm_forward_consumption()
    assert events[-1]["destination_consumed"] is True



class _Scalar:
    def __init__(self, value: int):
        self._value = value

    def item(self) -> int:
        return self._value


class _Mask:
    def __init__(self, true_count: int):
        self.true_count = true_count

    def sum(self) -> _Scalar:
        return _Scalar(self.true_count)


class _FakeEngine:
    def __init__(self, *, retrieved: int, transferred_bytes: int):
        self.retrieved = retrieved
        self.transferred_bytes = transferred_bytes

    def _process_tokens_internal(self, *_args, **_kwargs):
        return ([object()], self.transferred_bytes)

    def retrieve(self, tokens, mask, **kwargs):
        self._process_tokens_internal(tokens, mask, _Mask(0), **kwargs)
        return _Mask(self.retrieved)


@dataclass
class _Clock:
    values: list[float]

    def __call__(self) -> float:
        return self.values.pop(0)

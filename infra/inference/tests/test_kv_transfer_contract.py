"""Backend-neutral KV identity, transfer contract, and metadata lifecycle tests."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from infra.inference.kv_transfer import (
    CompatibilitySpec,
    ConfirmOutcome,
    ConfirmResult,
    InvalidateOutcome,
    InvalidateResult,
    KVTransferBackend,
    LookupOutcome,
    LookupResult,
    MetadataDirectory,
    PrefixIdentity,
    TransferOutcome,
    TransferResult,
)


def compatibility(**overrides: str) -> CompatibilitySpec:
    values = {
        "model_id": "Qwen/Qwen3-0.6B",
        "model_revision": "0123456789abcdef",
        "tokenizer_revision": "fedcba9876543210",
        "chat_template_version": "qwen3-chat-v1",
        "prefix_contract_version": "prefix-v2",
        "weight_dtype": "bfloat16",
        "kv_dtype": "fp8_e4m3",
        "adapter_namespace": "none",
        "cache_namespace": "default",
        "engine_version": "vllm-0.10.2",
        "block_layout_version": "vllm-block-v1-size-16",
    }
    values.update(overrides)
    return CompatibilitySpec(**values)


def identity(tokens: tuple[int, ...] = (1, 2, 3), **overrides: str) -> PrefixIdentity:
    return PrefixIdentity.build(tokens, compatibility(**overrides))


def test_prefix_identity_is_deterministic_and_token_order_is_exact() -> None:
    first = identity((101, 202, 303))
    second = identity((101, 202, 303))

    assert first == second
    assert first.value.startswith("sha256:")
    assert len(first.value) == len("sha256:") + 64
    assert first.token_count == 3
    assert identity((101, 303, 202)) != first
    assert identity((101, 202, 303, 0)) != first


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("model_id", "Qwen/Qwen3-1.7B"),
        ("model_revision", "other-model-revision"),
        ("tokenizer_revision", "other-tokenizer-revision"),
        ("chat_template_version", "qwen3-chat-v2"),
        ("prefix_contract_version", "prefix-v3"),
        ("weight_dtype", "float16"),
        ("kv_dtype", "bfloat16"),
        ("adapter_namespace", "adapter/acme-v1"),
        ("cache_namespace", "tenant-cache-v2"),
        ("engine_version", "vllm-0.11.0"),
        ("block_layout_version", "vllm-block-v2-size-32"),
    ],
)
def test_every_compatibility_field_separates_identity(field: str, replacement: str) -> None:
    base = compatibility()

    assert PrefixIdentity.build((1, 2, 3), replace(base, **{field: replacement})) != (
        PrefixIdentity.build((1, 2, 3), base)
    )


def test_compatibility_is_frozen_and_incomplete_configuration_fails_closed() -> None:
    spec = compatibility()
    with pytest.raises(FrozenInstanceError):
        spec.model_revision = "moving-tag"  # type: ignore[misc]

    for field in spec.as_dict():
        with pytest.raises(ValueError, match=field):
            compatibility(**{field: "  "})
    with pytest.raises(ValueError, match="token_ids"):
        PrefixIdentity.build((), spec)
    with pytest.raises(ValueError, match="token_ids"):
        PrefixIdentity.build((1, -1), spec)
    with pytest.raises(TypeError, match="token_ids"):
        PrefixIdentity.build((1, True), spec)


def test_compatibility_namespace_is_exact_and_independent_of_tokens() -> None:
    one = identity((1, 2, 3))
    two = identity((4, 5, 6))

    assert one.compatibility_namespace == two.compatibility_namespace
    assert one.compatibility_namespace.startswith("kv-compat-v1:sha256:")
    assert identity((1, 2, 3), kv_dtype="bfloat16").compatibility_namespace != (
        one.compatibility_namespace
    )


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (
            {
                "value": "client-label",
                "compatibility_namespace": "kv-compat-v1:sha256:" + "a" * 64,
                "token_count": 3,
            },
            "value",
        ),
        (
            {
                "value": "sha256:" + "a" * 64,
                "compatibility_namespace": "cache-label",
                "token_count": 3,
            },
            "compatibility_namespace",
        ),
        (
            {
                "value": "sha256:" + "a" * 64,
                "compatibility_namespace": "kv-compat-v1:sha256:" + "b" * 64,
                "token_count": 0,
            },
            "token_count",
        ),
    ],
)
def test_deserialized_prefix_identity_fails_closed(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        PrefixIdentity(**kwargs)


class _ContractExample:
    async def lookup(self, prefix_identity, destination, compatibility_namespace) -> LookupResult:
        return LookupResult(LookupOutcome.MISSING, prefix_identity, destination)

    async def transfer(
        self, prefix_identity, source_or_store, destination, deadline
    ) -> TransferResult:
        return TransferResult(
            TransferOutcome.TIMEOUT,
            prefix_identity,
            source_or_store,
            destination,
            detail="deadline elapsed",
        )

    async def confirm(self, prefix_identity, destination) -> ConfirmResult:
        return ConfirmResult(ConfirmOutcome.MISSING, prefix_identity, destination)

    async def invalidate(self, prefix_identity, location, reason) -> InvalidateResult:
        return InvalidateResult(InvalidateOutcome.MISSING, prefix_identity, location, reason=reason)


def test_async_protocol_and_typed_failure_outcomes() -> None:
    backend: KVTransferBackend = _ContractExample()
    key = identity()

    async def exercise() -> None:
        lookup = await backend.lookup(key, "worker-b", key.compatibility_namespace)
        transfer = await backend.transfer(key, "worker-a", "worker-b", 123.0)
        confirm = await backend.confirm(key, "worker-b")
        invalidation = await backend.invalidate(key, "worker-b", "worker_restart")
        assert lookup.outcome is LookupOutcome.MISSING
        assert transfer.outcome is TransferOutcome.TIMEOUT
        assert confirm.outcome is ConfirmOutcome.MISSING
        assert invalidation.outcome is InvalidateOutcome.MISSING

    asyncio.run(exercise())

    assert {outcome.value for outcome in TransferOutcome} >= {
        "transferred",
        "already_present",
        "missing",
        "partial",
        "incompatible",
        "timeout",
        "cancelled",
        "failed",
    }


def test_success_outcomes_fail_closed_without_positive_evidence() -> None:
    key = identity()
    with pytest.raises(ValueError, match="transferred_tokens"):
        TransferResult(
            TransferOutcome.TRANSFERRED,
            key,
            "worker-a",
            "worker-b",
            transferred_tokens=0,
            transferred_bytes=96,
            duration_ms=1.0,
        )
    with pytest.raises(ValueError, match="transferred_bytes"):
        TransferResult(
            TransferOutcome.TRANSFERRED,
            key,
            "worker-a",
            "worker-b",
            transferred_tokens=3,
            transferred_bytes=0,
            duration_ms=1.0,
        )
    with pytest.raises(ValueError, match="duration_ms"):
        TransferResult(
            TransferOutcome.TRANSFERRED,
            key,
            "worker-a",
            "worker-b",
            transferred_tokens=3,
            transferred_bytes=96,
        )
    with pytest.raises(ValueError, match="reusable_tokens"):
        ConfirmResult(ConfirmOutcome.AVAILABLE, key, "worker-b", available_bytes=96)

    success = TransferResult(
        TransferOutcome.TRANSFERRED,
        key,
        "worker-a",
        "worker-b",
        transferred_tokens=3,
        transferred_bytes=96,
        duration_ms=1.0,
    )
    assert (success.transferred_tokens, success.transferred_bytes) == (3, 96)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_directory_distinguishes_local_remote_missing_incompatible_and_partial() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=4, ttl_seconds=30, clock=clock)
    key = identity()
    directory.observe_worker("worker-a", "pod-a-1")
    directory.observe_worker("worker-b", "pod-b-1")

    assert directory.lookup(key, "worker-b", key.compatibility_namespace).outcome is (
        LookupOutcome.MISSING
    )
    directory.upsert(
        key,
        "worker-a",
        worker_generation="pod-a-1",
        reusable_tokens=3,
        stored_bytes=96,
    )
    remote = directory.lookup(key, "worker-b", key.compatibility_namespace)
    assert (remote.outcome, remote.source_or_store) == (LookupOutcome.REMOTE, "worker-a")
    assert (remote.reusable_tokens, remote.stored_bytes) == (3, 96)

    directory.upsert(
        key,
        "worker-b",
        worker_generation="pod-b-1",
        reusable_tokens=2,
        stored_bytes=64,
        complete=False,
    )
    assert directory.lookup(key, "worker-b", key.compatibility_namespace).outcome is (
        LookupOutcome.PARTIAL
    )

    wrong_namespace = identity(kv_dtype="bfloat16").compatibility_namespace
    assert directory.lookup(key, "worker-b", wrong_namespace).outcome is (
        LookupOutcome.INCOMPATIBLE
    )


def test_directory_ttl_returns_stale_then_removes_the_expired_belief() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=2, ttl_seconds=10, clock=clock)
    key = identity()
    directory.observe_worker("worker-a", "pod-a-1")
    directory.upsert(
        key,
        "worker-a",
        worker_generation="pod-a-1",
        reusable_tokens=3,
        stored_bytes=96,
    )

    clock.now += 10
    stale = directory.lookup(key, "worker-a", key.compatibility_namespace)
    assert stale.outcome is LookupOutcome.STALE
    assert stale.detail == "ttl_expired"
    assert directory.lookup(key, "worker-a", key.compatibility_namespace).outcome is (
        LookupOutcome.MISSING
    )


def test_multiple_expired_remote_entries_are_purged_without_iteration_errors() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=3, ttl_seconds=10, clock=clock)
    key = identity()
    for location in ("worker-a", "store-a"):
        directory.observe_worker(location, f"{location}-generation-1")
        directory.upsert(
            key,
            location,
            worker_generation=f"{location}-generation-1",
            reusable_tokens=3,
            stored_bytes=96,
        )

    clock.now += 11
    assert directory.lookup(key, "worker-b", key.compatibility_namespace).outcome is (
        LookupOutcome.STALE
    )
    assert len(directory) == 0


def test_directory_capacity_is_bounded_lru() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=2, ttl_seconds=30, clock=clock)
    directory.observe_worker("worker-a", "pod-a-1")
    one, two, three = identity((1,)), identity((2,)), identity((3,))

    for key in (one, two):
        directory.upsert(
            key,
            "worker-a",
            worker_generation="pod-a-1",
            reusable_tokens=1,
            stored_bytes=32,
        )
    directory.lookup(one, "worker-a", one.compatibility_namespace)  # one is now most recent
    directory.upsert(
        three,
        "worker-a",
        worker_generation="pod-a-1",
        reusable_tokens=1,
        stored_bytes=32,
    )

    assert len(directory) == 2
    assert directory.lookup(two, "worker-a", two.compatibility_namespace).outcome is (
        LookupOutcome.MISSING
    )
    assert directory.evictions["capacity"] == 1


def test_worker_generation_change_prevents_ghost_confirmation() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=2, ttl_seconds=30, clock=clock)
    key = identity()
    directory.observe_worker("worker-b", "pod-b-1")
    directory.upsert(
        key,
        "worker-b",
        worker_generation="pod-b-1",
        reusable_tokens=3,
        stored_bytes=96,
    )
    assert directory.confirm(key, "worker-b").outcome is ConfirmOutcome.AVAILABLE

    assert directory.observe_worker("worker-b", "pod-b-2") == 1
    ghost = directory.confirm(key, "worker-b")
    assert ghost.outcome is ConfirmOutcome.STALE
    assert ghost.detail == "worker_generation_changed"
    assert directory.lookup(key, "worker-b", key.compatibility_namespace).outcome is (
        LookupOutcome.STALE
    )


def test_namespace_and_explicit_invalidation_are_scoped() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=4, ttl_seconds=30, clock=clock)
    first = identity((1,))
    second = identity((2,), adapter_namespace="adapter/acme-v1")
    directory.observe_worker("worker-a", "pod-a-1")
    for key in (first, second):
        directory.upsert(
            key,
            "worker-a",
            worker_generation="pod-a-1",
            reusable_tokens=1,
            stored_bytes=32,
        )

    assert directory.invalidate_namespace(first.compatibility_namespace, "adapter_unloaded") == 1
    assert directory.confirm(first, "worker-a").outcome is ConfirmOutcome.STALE
    assert directory.confirm(second, "worker-a").outcome is ConfirmOutcome.AVAILABLE

    removed = directory.invalidate(first, "worker-a", "blocks_evicted")
    assert removed.outcome is InvalidateOutcome.INVALIDATED
    assert directory.confirm(first, "worker-a").outcome is ConfirmOutcome.MISSING
    assert directory.invalidate(first, "worker-a", "blocks_evicted").outcome is (
        InvalidateOutcome.MISSING
    )


def test_directory_results_are_explicitly_metadata_only() -> None:
    clock = FakeClock()
    directory = MetadataDirectory(capacity=1, ttl_seconds=30, clock=clock)
    key = identity()
    directory.observe_worker("worker-a", "pod-a-1")
    directory.upsert(
        key,
        "worker-a",
        worker_generation="pod-a-1",
        reusable_tokens=3,
        stored_bytes=96,
    )

    lookup = directory.lookup(key, "worker-a", key.compatibility_namespace)
    confirm = directory.confirm(key, "worker-a")
    assert lookup.metadata_only is True
    assert confirm.metadata_only is True
    assert not hasattr(directory, "transfer")

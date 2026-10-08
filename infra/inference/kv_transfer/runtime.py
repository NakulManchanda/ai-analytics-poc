"""Fail-closed LMCache/Mooncake runtime hooks for real KV reuse evidence.

The runtime does not move tensors itself. LMCache owns lookup, store, and retrieval while this
module binds every cache key to the worker's compatibility namespace and records the bytes and
tokens that LMCache actually loaded into vLLM's paged KV cache.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from .identity import CompatibilitySpec, PrefixIdentity
from .metrics import record as record_metrics

LMCACHE_COMPATIBILITY_TAG = "lmcache.tag.compatibility"
HOP_ACTION_KEY = "lmcache.hop.action"
HOP_NAMESPACE_KEY = "lmcache.hop.compatibility_namespace"
HOP_PREFIX_ID_KEY = "lmcache.hop.prefix_identity"
HOP_PREFIX_VERSION_KEY = "lmcache.hop.prefix_identity_version"

SUPPORTED_VLLM_VERSION = "0.11.0"
SUPPORTED_LMCACHE_VERSION = "0.3.9"

log = logging.getLogger("inference.kv_transfer")


class KVTransferIntegrityError(RuntimeError):
    """Raised before invalid or only partially loaded KV can be consumed."""


def emit_json_evidence(event: dict[str, Any]) -> None:
    """Write one machine-readable event; raw prompts and token IDs are never included."""

    record_metrics(event)
    log.info(json.dumps(event, sort_keys=True, separators=(",", ":")))


class KVHopRuntime:
    """Coordinates worker-owned identity, policy gating, and engine instrumentation."""

    def __init__(
        self,
        *,
        enabled: bool,
        compatibility: CompatibilitySpec,
        destination_worker: str,
        source_worker_or_store: str = "mooncake_store",
        emit: Callable[[dict[str, Any]], None] = emit_json_evidence,
        clock: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], float] = time.time,
        synchronize: Callable[[], None] | None = None,
    ) -> None:
        if not destination_worker.strip():
            raise ValueError("destination_worker must be non-empty")
        if not source_worker_or_store.strip():
            raise ValueError("source_worker_or_store must be non-empty")
        self.enabled = enabled
        self.compatibility = compatibility
        self.destination_worker = destination_worker
        self.source_worker_or_store = source_worker_or_store
        self._emit = emit
        self._clock = clock
        self._wall_clock = wall_clock
        self._synchronize = synchronize or _cuda_synchronize
        self._thread_state = threading.local()
        self._instrumented_engine_ids: set[int] = set()
        self._pending_lock = threading.Lock()
        self._pending_consumption: list[tuple[dict[str, Any], float]] = []

    def get_num_new_matched_tokens(
        self,
        request: Any,
        num_computed_tokens: int,
        upstream: Callable[[Any, int], tuple[int | None, bool]],
    ) -> tuple[int | None, bool]:
        """Apply trusted identity and invoke vLLM 0.11's exact connector lookup contract."""

        params, identity = self._prepare_request(request)
        if not self.enabled or not _is_load_requested(params):
            params[HOP_ACTION_KEY] = "recompute"
            local_tokens = max(0, int(num_computed_tokens))
            params["lmcache.hop.reusable_tokens"] = local_tokens
            fallback = "recompute" if not local_tokens else "none"
            params.setdefault("lmcache.hop.fallback_action", fallback)
            local_result = "local_reuse" if local_tokens else "recomputed"
            self._emit(
                self._base_evidence(params, identity)
                | {
                    "event": "kv_hop",
                    "hop_result": local_result,
                    "transferred_tokens": 0,
                    "transferred_bytes": 0,
                    "lookup_ms": 0.0,
                    "transfer_ms": 0.0,
                    "confirm_ms": 0.0,
                    "fallback_action": "none" if local_tokens else fallback,
                    "confirm_result": "available" if local_tokens else "missing",
                    "destination_reused_tokens": local_tokens,
                    # Scheduler allocation proves a local hit, not a completed model
                    # forward.  The live smoke combines this with the successful
                    # response and an isolated vLLM metric window.
                    "destination_consumed": False,
                    "consumed": False,
                }
            )
            return 0, False

        deadline = params.get("lmcache.hop.deadline_epoch_s")
        if deadline is not None and _as_float(deadline, "deadline_epoch_s") <= self._wall_clock():
            params[HOP_ACTION_KEY] = "recompute"
            params["lmcache.hop.fallback_action"] = "recompute_deadline_expired"
            self._emit(
                self._base_evidence(params, identity)
                | {
                    "event": "kv_hop_lookup",
                    "hop_result": "timeout",
                    "confirm_result": "missing",
                    "fallback_action": "recompute_deadline_expired",
                }
            )
            return 0, False

        params[HOP_ACTION_KEY] = "load"
        started = self._clock()
        try:
            result = upstream(request, num_computed_tokens)
        except Exception as exc:
            self._emit(
                self._base_evidence(params, identity)
                | {
                    "event": "kv_hop_lookup",
                    "hop_result": "failed",
                    "confirm_result": "missing",
                    "fallback_action": "fail_request",
                    "error_type": type(exc).__name__,
                }
            )
            raise
        lookup_ms = (self._clock() - started) * 1000
        if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[1], bool):
            raise KVTransferIntegrityError(
                "vLLM 0.11 connector lookup must return (Optional[int], bool)"
            )
        reusable_tokens = result[0]
        if reusable_tokens is not None and reusable_tokens < 0:
            raise KVTransferIntegrityError("LMCache returned a negative external token count")
        params["lmcache.hop.lookup_ms"] = lookup_ms
        params["lmcache.hop.reusable_tokens"] = reusable_tokens or 0
        if not reusable_tokens:
            params["lmcache.hop.fallback_action"] = "recompute_missing"
            self._emit(
                self._base_evidence(params, identity)
                | {
                    "event": "kv_hop_lookup",
                    "lookup_ms": lookup_ms,
                    "hop_result": "missing",
                    "confirm_result": "missing",
                    "fallback_action": "recompute_missing",
                }
            )
        return result

    def instrument_engine(self, engine: Any) -> None:
        """Intercept one LMCacheEngine's real retrieve path without replacing storage logic."""

        if id(engine) in self._instrumented_engine_ids:
            return
        original_retrieve = engine.retrieve
        original_process = engine._process_tokens_internal

        def process_tokens(*args: Any, **kwargs: Any):
            result = original_process(*args, **kwargs)
            if not isinstance(result, tuple) or len(result) != 2:
                raise KVTransferIntegrityError(
                    "LMCache _process_tokens_internal returned an unknown result"
                )
            transferred_bytes = result[1]
            if not isinstance(transferred_bytes, int) or transferred_bytes < 0:
                raise KVTransferIntegrityError("LMCache reported invalid transferred bytes")
            self._thread_state.transferred_bytes = transferred_bytes
            return result

        def retrieve(tokens: Any, mask: Any = None, **kwargs: Any):
            request_configs = kwargs.get("request_configs") or {}
            identity = self._validate_runtime_identity(request_configs)
            expected_tokens = _mask_count(mask, len(tokens))
            self._thread_state.transferred_bytes = 0
            started = self._clock()
            try:
                ret_mask = original_retrieve(tokens, mask, **kwargs)
                transfer_done = self._clock()
                retrieved_tokens = _mask_count(ret_mask, 0)
                transferred_bytes = int(getattr(self._thread_state, "transferred_bytes", 0))
                if retrieved_tokens != expected_tokens:
                    event = self._retrieval_evidence(
                        request_configs,
                        identity,
                        retrieved_tokens=retrieved_tokens,
                        transferred_bytes=transferred_bytes,
                        transfer_ms=(transfer_done - started) * 1000,
                        confirm_ms=0.0,
                        hop_result="failed",
                        confirm_result="missing",
                        destination_consumed=False,
                        fallback_action="fail_request_partial_retrieval",
                    )
                    self._emit(event)
                    raise KVTransferIntegrityError(
                        f"partial KV retrieval: expected {expected_tokens} tokens, "
                        f"loaded {retrieved_tokens}"
                    )
                if retrieved_tokens <= 0 or transferred_bytes <= 0:
                    event = self._retrieval_evidence(
                        request_configs,
                        identity,
                        retrieved_tokens=retrieved_tokens,
                        transferred_bytes=transferred_bytes,
                        transfer_ms=(transfer_done - started) * 1000,
                        confirm_ms=0.0,
                        hop_result="failed",
                        confirm_result="missing",
                        destination_consumed=False,
                        fallback_action="fail_request_empty_retrieval",
                    )
                    self._emit(event)
                    raise KVTransferIntegrityError(
                        "LMCache retrieval returned no provable KV bytes/tokens"
                    )

                self._synchronize()
                available_at = self._clock()
                event = self._retrieval_evidence(
                    request_configs,
                    identity,
                    retrieved_tokens=retrieved_tokens,
                    transferred_bytes=transferred_bytes,
                    transfer_ms=(transfer_done - started) * 1000,
                    confirm_ms=(available_at - transfer_done) * 1000,
                    hop_result="transferred",
                    confirm_result="available",
                    destination_consumed=False,
                    fallback_action="none",
                )
                event["event"] = "kv_hop_available"
                self._emit(event)
                with self._pending_lock:
                    self._pending_consumption.append((event, transfer_done))
                return ret_mask
            except KVTransferIntegrityError:
                raise
            except Exception as exc:
                self._emit(
                    self._retrieval_evidence(
                        request_configs,
                        identity,
                        retrieved_tokens=0,
                        transferred_bytes=int(getattr(self._thread_state, "transferred_bytes", 0)),
                        transfer_ms=(self._clock() - started) * 1000,
                        confirm_ms=0.0,
                        hop_result="failed",
                        confirm_result="missing",
                        destination_consumed=False,
                        fallback_action="fail_request",
                        error_type=type(exc).__name__,
                    )
                )
                raise
            finally:
                self._thread_state.transferred_bytes = 0

        engine._process_tokens_internal = process_tokens
        engine.retrieve = retrieve
        self._instrumented_engine_ids.add(id(engine))

    def begin_forward(self) -> None:
        """Start a model-forward boundary and fail any unconfirmed prior retrieval."""

        self.fail_forward("previous_forward_not_confirmed")

    def confirm_forward_consumption(self) -> None:
        """Record consumption after vLLM completes the forward/save boundary."""

        with self._pending_lock:
            pending = self._pending_consumption
            self._pending_consumption = []
        if not pending:
            return
        confirmed_at = self._clock()
        for available, transfer_done in pending:
            final = dict(available)
            final.update(
                event="kv_hop",
                confirm_ms=(confirmed_at - transfer_done) * 1000,
                destination_reused_tokens=available["transferred_tokens"],
                destination_consumed=True,
                consumed=True,
            )
            self._emit(final)

    def fail_forward(self, reason: str) -> None:
        """Discard pending availability so it cannot be confirmed by a later request."""

        with self._pending_lock:
            pending = self._pending_consumption
            self._pending_consumption = []
        for available, _transfer_done in pending:
            failed = dict(available)
            failed.update(
                event="kv_hop",
                hop_result="failed",
                confirm_result="stale",
                fallback_action=reason,
                destination_reused_tokens=0,
                destination_consumed=False,
                consumed=False,
            )
            self._emit(failed)

    def _prepare_request(self, request: Any) -> tuple[MutableMapping[str, Any], PrefixIdentity]:
        token_ids = getattr(request, "prompt_token_ids", None)
        identity = PrefixIdentity.build(token_ids, self.compatibility)
        params = _kv_transfer_params(request)
        worker_owned = {
            LMCACHE_COMPATIBILITY_TAG: identity.compatibility_namespace,
            HOP_NAMESPACE_KEY: identity.compatibility_namespace,
            HOP_PREFIX_ID_KEY: identity.value,
            HOP_PREFIX_VERSION_KEY: identity.version,
        }
        for key, expected in worker_owned.items():
            supplied = params.get(key)
            if supplied is not None and supplied != expected:
                raise KVTransferIntegrityError(f"{key} disagrees with worker-derived identity")
            params[key] = expected
        params["lmcache.hop.destination_worker"] = self.destination_worker
        params.setdefault("lmcache.hop.source_worker_or_store", self.source_worker_or_store)
        if params.get("lmcache.hop.reason") not in {
            "local",
            "local_prefix_present",
            "no_prior_worker",
            "transfer_disabled",
            "independently_warmed_destination",
            "remote_prefix_candidate",
            "deadline_recompute",
            "experiment_on",
            "experiment_off",
        }:
            params["lmcache.hop.reason"] = "unspecified"
        return params, identity

    def _validate_runtime_identity(self, params: Mapping[str, Any]) -> PrefixIdentity:
        expected_namespace = self.compatibility.namespace
        if params.get(LMCACHE_COMPATIBILITY_TAG) != expected_namespace:
            raise KVTransferIntegrityError(
                "LMCache retrieval is missing the worker-derived compatibility tag"
            )
        if params.get(HOP_NAMESPACE_KEY) != expected_namespace:
            raise KVTransferIntegrityError(
                "retrieval compatibility namespace differs from worker configuration"
            )
        prefix_value = params.get(HOP_PREFIX_ID_KEY)
        prefix_version = params.get(HOP_PREFIX_VERSION_KEY)
        if not isinstance(prefix_value, str) or not prefix_value.startswith("sha256:"):
            raise KVTransferIntegrityError("retrieval is missing canonical prefix identity")
        if prefix_version != "kv-prefix-identity-v1":
            raise KVTransferIntegrityError("retrieval prefix identity version is unsupported")
        return PrefixIdentity(
            value=prefix_value,
            compatibility_namespace=expected_namespace,
            token_count=int(params.get("lmcache.hop.reusable_tokens", 0)),
            version=prefix_version,
        )

    def _base_evidence(self, params: Mapping[str, Any], identity: PrefixIdentity) -> dict[str, Any]:
        return {
            "request_id": params.get("lmcache.hop.request_id"),
            "conversation_id": params.get("lmcache.hop.conversation_id"),
            "agent_step": params.get("lmcache.hop.agent_step"),
            "prefix_identity": identity.value,
            "prefix_identity_version": identity.version,
            "source_worker_or_store": params.get(
                "lmcache.hop.source_worker_or_store", self.source_worker_or_store
            ),
            "destination_worker": self.destination_worker,
            "compatibility_namespace": identity.compatibility_namespace,
            "hop_decision_reason": params.get("lmcache.hop.reason"),
            "reusable_tokens": int(params.get("lmcache.hop.reusable_tokens", 0)),
            "evidence_scope": "per_request",
        }

    def _retrieval_evidence(
        self,
        params: Mapping[str, Any],
        identity: PrefixIdentity,
        *,
        retrieved_tokens: int,
        transferred_bytes: int,
        transfer_ms: float,
        confirm_ms: float,
        hop_result: str,
        confirm_result: str,
        destination_consumed: bool,
        fallback_action: str,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        event = self._base_evidence(params, identity) | {
            "event": "kv_hop",
            "hop_result": hop_result,
            "transferred_tokens": retrieved_tokens,
            "transferred_bytes": transferred_bytes,
            "lookup_ms": float(params.get("lmcache.hop.lookup_ms", 0.0)),
            "transfer_ms": transfer_ms,
            "confirm_ms": confirm_ms,
            "fallback_action": fallback_action,
            "confirm_result": confirm_result,
            "destination_reused_tokens": retrieved_tokens if destination_consumed else 0,
            "destination_consumed": destination_consumed,
            "consumed": destination_consumed,
        }
        if error_type is not None:
            event["error_type"] = error_type
        return event


def validate_lmcache_runtime(
    *,
    tensor_parallel_size: int,
    use_layerwise: bool,
    enable_async_loading: bool,
    remote_url: str | None,
    local_cpu: bool,
) -> None:
    """Reject modes whose completion and provenance cannot be proven by this slice."""

    if tensor_parallel_size != 1:
        raise RuntimeError("KV hop evidence currently requires tensor_parallel_size=1")
    if use_layerwise:
        raise RuntimeError("layerwise LMCache retrieval is not supported")
    if enable_async_loading:
        raise RuntimeError("async LMCache loading is not supported")
    if not remote_url or not remote_url.startswith("mooncakestore://"):
        raise RuntimeError("KV hop evidence requires a Mooncake Store remote_url")
    if local_cpu:
        raise RuntimeError("local_cpu must be false so Mooncake provenance is unambiguous")


def compatibility_from_vllm(
    vllm_config: Any,
    environ: Mapping[str, str] | None = None,
    *,
    package_version: Callable[[str], str] = importlib.metadata.version,
) -> CompatibilitySpec:
    """Build the exact compatibility namespace from immutable engine inputs."""

    env = os.environ if environ is None else environ
    model = vllm_config.model_config
    cache = vllm_config.cache_config
    parallel = vllm_config.parallel_config
    engine_version = package_version("vllm")
    if engine_version != SUPPORTED_VLLM_VERSION:
        raise RuntimeError(
            f"unsupported vLLM version {engine_version}; expected {SUPPORTED_VLLM_VERSION}"
        )
    lmcache_version = package_version("lmcache")
    if lmcache_version != SUPPORTED_LMCACHE_VERSION:
        raise RuntimeError(
            f"unsupported LMCache version {lmcache_version}; expected {SUPPORTED_LMCACHE_VERSION}"
        )
    model_revision = getattr(model, "revision", None) or env.get("MODEL_REVISION")
    tokenizer_revision = getattr(model, "tokenizer_revision", None) or env.get(
        "KV_TOKENIZER_REVISION"
    )
    weight_dtype = _dtype_name(getattr(model, "dtype", None))
    configured_kv_dtype = getattr(cache, "cache_dtype", None)
    kv_dtype = (
        weight_dtype if configured_kv_dtype in (None, "auto") else _dtype_name(configured_kv_dtype)
    )
    lora_config = getattr(vllm_config, "lora_config", None)
    adapter_namespace = env.get("KV_ADAPTER_NAMESPACE")
    if adapter_namespace is None and lora_config is None:
        adapter_namespace = "none"
    block_size = getattr(cache, "block_size", None)
    tp = getattr(parallel, "tensor_parallel_size", None)
    return CompatibilitySpec(
        model_id=str(getattr(model, "model", "")),
        model_revision=_required(model_revision, "MODEL_REVISION"),
        tokenizer_revision=_required(tokenizer_revision, "KV_TOKENIZER_REVISION"),
        chat_template_version=_required(env.get("KV_TEMPLATE_VERSION"), "KV_TEMPLATE_VERSION"),
        prefix_contract_version=_required(
            env.get("KV_PREFIX_CONTRACT_VERSION"), "KV_PREFIX_CONTRACT_VERSION"
        ),
        weight_dtype=weight_dtype,
        kv_dtype=kv_dtype,
        adapter_namespace=_required(adapter_namespace, "KV_ADAPTER_NAMESPACE"),
        cache_namespace=_required(env.get("KV_CACHE_NAMESPACE"), "KV_CACHE_NAMESPACE"),
        engine_version=engine_version,
        block_layout_version=f"vllm-v1:block-size={block_size}:tp={tp}",
    )


def _kv_transfer_params(request: Any) -> MutableMapping[str, Any]:
    sampling = getattr(request, "sampling_params", None)
    if sampling is None:
        raise KVTransferIntegrityError("request has no sampling_params")
    if sampling.extra_args is None:
        sampling.extra_args = {}
    if not isinstance(sampling.extra_args, MutableMapping):
        raise KVTransferIntegrityError("sampling_params.extra_args must be a mapping")
    params = sampling.extra_args.setdefault("kv_transfer_params", {})
    if not isinstance(params, MutableMapping):
        raise KVTransferIntegrityError("kv_transfer_params must be a mapping")
    return params


def _is_load_requested(params: Mapping[str, Any]) -> bool:
    return params.get("lmcache.hop.load") is True


def _mask_count(mask: Any, default: int) -> int:
    if mask is None:
        return default
    value = mask.sum()
    if hasattr(value, "item"):
        value = value.item()
    return int(value)


def _as_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise KVTransferIntegrityError(f"lmcache.hop.{name} must be numeric") from exc


def _dtype_name(value: Any) -> str:
    if value is None:
        raise RuntimeError("dtype must be resolved before KV transfer starts")
    result = str(value).removeprefix("torch.")
    if not result or result == "auto":
        raise RuntimeError("dtype must be concrete before KV transfer starts")
    return result


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{name} must be an explicit immutable value")
    return value


def _cuda_synchronize() -> None:
    import torch

    if not torch.cuda.is_available():
        raise KVTransferIntegrityError("CUDA is unavailable while confirming KV load")
    torch.cuda.synchronize()

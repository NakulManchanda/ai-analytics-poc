"""vLLM 0.11 dynamic connector for LMCache 0.3.9 backed by Mooncake Store."""

from __future__ import annotations

import os
from typing import Any

from .runtime import (
    KVHopRuntime,
    compatibility_from_vllm,
    validate_lmcache_runtime,
)

try:
    from vllm.distributed.kv_transfer.kv_connector.v1 import KVConnectorRole
    from vllm.distributed.kv_transfer.kv_connector.v1.lmcache_connector import (
        LMCacheConnectorV1,
    )
except ImportError as exc:  # Keep non-GPU unit tests importable; construction still fails closed.
    _CONNECTOR_IMPORT_ERROR: ImportError | None = exc

    class LMCacheConnectorV1:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(
                "MooncakeKVConnector requires vLLM 0.11.0 and LMCache 0.3.9"
            ) from _CONNECTOR_IMPORT_ERROR

    class KVConnectorRole:  # type: ignore[no-redef]
        WORKER = "worker"

else:
    _CONNECTOR_IMPORT_ERROR = None


class MooncakeKVConnector(LMCacheConnectorV1):
    """LMCache's native connector plus worker-owned identity and proof hooks.

    Configure vLLM with ``kv_connector_module_path`` pointing to this module and
    ``kv_connector`` set to ``MooncakeKVConnector``. The actual KV storage and movement remains
    inside LMCache/Mooncake; this subclass only gates lookup and observes completed retrieval.
    """

    def __init__(self, vllm_config: Any, role: Any) -> None:
        if _CONNECTOR_IMPORT_ERROR is not None:
            super().__init__(vllm_config, role)
            return

        compatibility = compatibility_from_vllm(vllm_config)
        self._hop_runtime = KVHopRuntime(
            enabled=os.getenv("KV_HOP_ENABLED") == "1",
            compatibility=compatibility,
            destination_worker=_required_env("KV_WORKER_ID"),
            source_worker_or_store=os.getenv("KV_HOP_SOURCE_STORE", "mooncake_store"),
        )
        super().__init__(vllm_config, role)

        lmcache_impl = self._lmcache_engine
        lmcache_config = lmcache_impl.config
        validate_lmcache_runtime(
            tensor_parallel_size=vllm_config.parallel_config.tensor_parallel_size,
            use_layerwise=bool(lmcache_config.use_layerwise),
            enable_async_loading=bool(lmcache_config.enable_async_loading),
            remote_url=lmcache_config.remote_url,
            local_cpu=bool(lmcache_config.local_cpu),
        )
        if role == KVConnectorRole.WORKER:
            engine = lmcache_impl.lmcache_engine
            if engine is None:
                raise RuntimeError("LMCache worker engine was not initialized")
            self._hop_runtime.instrument_engine(engine)

    def get_num_new_matched_tokens(
        self,
        request: Any,
        num_computed_tokens: int,
    ) -> tuple[int | None, bool]:
        upstream = super().get_num_new_matched_tokens
        return self._hop_runtime.get_num_new_matched_tokens(
            request,
            num_computed_tokens,
            upstream,
        )

    def start_load_kv(self, forward_context: Any, **kwargs: Any) -> None:
        self._hop_runtime.begin_forward()
        try:
            return super().start_load_kv(forward_context, **kwargs)
        except Exception:
            self._hop_runtime.fail_forward("fail_request_load")
            raise

    def wait_for_save(self):
        try:
            result = super().wait_for_save()
        except Exception:
            self._hop_runtime.fail_forward("fail_request_forward_or_save")
            raise
        self._hop_runtime.confirm_forward_consumption()
        return result


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} must be set for Mooncake KV transfer")
    return value


__all__ = ["MooncakeKVConnector"]

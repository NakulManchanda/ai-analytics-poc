"""Canonical, fail-closed identity for reusable KV prefixes.

The identity hashes the exact ordered rendered token IDs together with every field that can
change the meaning or memory layout of their KV blocks.  Human-provided conversation, tenant,
or router-affinity labels are deliberately absent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass

IDENTITY_VERSION = "kv-prefix-identity-v1"
COMPATIBILITY_VERSION = "kv-compat-v1"


@dataclass(frozen=True)
class CompatibilitySpec:
    """Exact compatibility inputs required before reuse may be considered.

    Sentinel values such as ``"none"`` are acceptable when an adapter is intentionally absent;
    an empty or omitted value is not. Values are preserved byte-for-byte after UTF-8 encoding,
    so callers must provide immutable revisions rather than moving tags when exactness matters.
    """

    model_id: str
    model_revision: str
    tokenizer_revision: str
    chat_template_version: str
    prefix_contract_version: str
    weight_dtype: str
    kv_dtype: str
    adapter_namespace: str
    cache_namespace: str
    engine_version: str
    block_layout_version: str

    def __post_init__(self) -> None:
        for field, value in self.as_dict().items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be an explicit non-empty string")

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def namespace(self) -> str:
        encoded = _canonical_json(
            {"version": COMPATIBILITY_VERSION, "compatibility": self.as_dict()}
        )
        return f"{COMPATIBILITY_VERSION}:sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class PrefixIdentity:
    """SHA-256 identity of exact rendered token IDs and compatibility inputs."""

    value: str
    compatibility_namespace: str
    token_count: int
    version: str = IDENTITY_VERSION

    def __post_init__(self) -> None:
        if not _is_sha256(self.value, "sha256:"):
            raise ValueError("value must be a canonical SHA-256 identity")
        if not _is_sha256(self.compatibility_namespace, f"{COMPATIBILITY_VERSION}:sha256:"):
            raise ValueError("compatibility_namespace must be a canonical SHA-256 namespace")
        if isinstance(self.token_count, bool) or not isinstance(self.token_count, int):
            raise TypeError("token_count must be an integer")
        if self.token_count <= 0:
            raise ValueError("token_count must be positive")
        if self.version != IDENTITY_VERSION:
            raise ValueError(f"version must be {IDENTITY_VERSION}")

    @classmethod
    def build(
        cls, rendered_token_ids: Iterable[int], compatibility: CompatibilitySpec
    ) -> PrefixIdentity:
        token_ids = tuple(rendered_token_ids)
        if not token_ids:
            raise ValueError("token_ids must contain at least one rendered token")
        if any(
            isinstance(token_id, bool) or not isinstance(token_id, int) for token_id in token_ids
        ):
            raise TypeError("token_ids must contain integers")
        if any(token_id < 0 for token_id in token_ids):
            raise ValueError("token_ids must be non-negative")

        encoded = _canonical_json(
            {
                "version": IDENTITY_VERSION,
                "rendered_token_ids": token_ids,
                "compatibility": compatibility.as_dict(),
            }
        )
        return cls(
            value=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            compatibility_namespace=compatibility.namespace,
            token_count=len(token_ids),
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _is_sha256(value: object, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value[len(prefix) :]
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)

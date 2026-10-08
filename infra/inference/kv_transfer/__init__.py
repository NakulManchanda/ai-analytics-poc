"""Backend-neutral KV transfer identity, contract, and metadata directory."""

from .contract import (
    ConfirmOutcome,
    ConfirmResult,
    InvalidateOutcome,
    InvalidateResult,
    KVTransferBackend,
    LookupOutcome,
    LookupResult,
    TransferOutcome,
    TransferResult,
)
from .directory import DirectoryRecord, MetadataDirectory
from .identity import CompatibilitySpec, PrefixIdentity

__all__ = [
    "CompatibilitySpec",
    "ConfirmOutcome",
    "ConfirmResult",
    "DirectoryRecord",
    "InvalidateOutcome",
    "InvalidateResult",
    "KVTransferBackend",
    "LookupOutcome",
    "LookupResult",
    "MetadataDirectory",
    "PrefixIdentity",
    "TransferOutcome",
    "TransferResult",
]

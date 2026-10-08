"""Backend-neutral asynchronous contract for real KV transfer implementations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .identity import PrefixIdentity


class LookupOutcome(StrEnum):
    PRESENT = "present"
    REMOTE = "remote"
    MISSING = "missing"
    PARTIAL = "partial"
    STALE = "stale"
    INCOMPATIBLE = "incompatible"


class TransferOutcome(StrEnum):
    TRANSFERRED = "transferred"
    ALREADY_PRESENT = "already_present"
    MISSING = "missing"
    PARTIAL = "partial"
    INCOMPATIBLE = "incompatible"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConfirmOutcome(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    STALE = "stale"


class InvalidateOutcome(StrEnum):
    INVALIDATED = "invalidated"
    MISSING = "missing"


@dataclass(frozen=True)
class LookupResult:
    outcome: LookupOutcome
    prefix_identity: PrefixIdentity
    destination: str
    source_or_store: str | None = None
    reusable_tokens: int = 0
    stored_bytes: int = 0
    detail: str | None = None
    metadata_only: bool = False

    def __post_init__(self) -> None:
        _require_nonnegative("reusable_tokens", self.reusable_tokens)
        _require_nonnegative("stored_bytes", self.stored_bytes)


@dataclass(frozen=True)
class TransferResult:
    outcome: TransferOutcome
    prefix_identity: PrefixIdentity
    source_or_store: str
    destination: str
    transferred_tokens: int = 0
    transferred_bytes: int = 0
    duration_ms: float | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        _require_nonnegative("transferred_tokens", self.transferred_tokens)
        _require_nonnegative("transferred_bytes", self.transferred_bytes)
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.outcome is TransferOutcome.TRANSFERRED:
            if self.transferred_tokens <= 0:
                raise ValueError("transferred_tokens must be positive for transferred")
            if self.transferred_bytes <= 0:
                raise ValueError("transferred_bytes must be positive for transferred")
            if self.duration_ms is None:
                raise ValueError("duration_ms is required for transferred")


@dataclass(frozen=True)
class ConfirmResult:
    outcome: ConfirmOutcome
    prefix_identity: PrefixIdentity
    destination: str
    reusable_tokens: int = 0
    available_bytes: int = 0
    detail: str | None = None
    metadata_only: bool = False

    def __post_init__(self) -> None:
        _require_nonnegative("reusable_tokens", self.reusable_tokens)
        _require_nonnegative("available_bytes", self.available_bytes)
        if self.outcome is ConfirmOutcome.AVAILABLE:
            if self.reusable_tokens <= 0:
                raise ValueError("reusable_tokens must be positive for available")
            if self.available_bytes <= 0:
                raise ValueError("available_bytes must be positive for available")


@dataclass(frozen=True)
class InvalidateResult:
    outcome: InvalidateOutcome
    prefix_identity: PrefixIdentity
    location: str
    reason: str


class KVTransferBackend(Protocol):
    """A backend that can prove real KV movement and destination availability.

    ``deadline`` is an absolute monotonic-clock deadline. Implementations own cancellation and
    must report measured token/byte counts; the metadata directory is not an implementation of
    this protocol because it never moves or verifies KV tensors.
    """

    async def lookup(
        self,
        prefix_identity: PrefixIdentity,
        destination: str,
        compatibility_namespace: str,
    ) -> LookupResult: ...

    async def transfer(
        self,
        prefix_identity: PrefixIdentity,
        source_or_store: str,
        destination: str,
        deadline: float,
    ) -> TransferResult: ...

    async def confirm(self, prefix_identity: PrefixIdentity, destination: str) -> ConfirmResult: ...

    async def invalidate(
        self, prefix_identity: PrefixIdentity, location: str, reason: str
    ) -> InvalidateResult: ...


def _require_nonnegative(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")

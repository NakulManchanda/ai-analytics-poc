"""Bounded metadata directory for KV location beliefs.

This module stores provenance metadata only. A directory hit or ``AVAILABLE`` confirmation means
that the metadata belief is current with respect to TTL, invalidation, and worker generation. It
does not prove that a backend transferred tensors or that an engine consumed them.
"""

from __future__ import annotations

import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace

from .contract import (
    ConfirmOutcome,
    ConfirmResult,
    InvalidateOutcome,
    InvalidateResult,
    LookupOutcome,
    LookupResult,
)
from .identity import PrefixIdentity


@dataclass(frozen=True)
class DirectoryRecord:
    prefix_identity: PrefixIdentity
    location: str
    worker_generation: str
    reusable_tokens: int
    stored_bytes: int
    complete: bool
    observed_at: float
    last_accessed_at: float
    stale_reason: str | None = None


class MetadataDirectory:
    """TTL/capacity-bounded LRU of KV-location metadata beliefs."""

    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._records: OrderedDict[tuple[PrefixIdentity, str], DirectoryRecord] = OrderedDict()
        self._worker_generations: dict[str, str] = {}
        self.evictions: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._records)

    def observe_worker(self, location: str, generation: str) -> int:
        """Record a worker incarnation and stale beliefs from an older incarnation."""
        _require_text("location", location)
        _require_text("generation", generation)
        previous = self._worker_generations.get(location)
        self._worker_generations[location] = generation
        if previous is None or previous == generation:
            return 0
        return self._mark_stale(
            lambda record: record.location == location,
            reason="worker_generation_changed",
        )

    def upsert(
        self,
        prefix_identity: PrefixIdentity,
        location: str,
        *,
        worker_generation: str,
        reusable_tokens: int,
        stored_bytes: int,
        complete: bool = True,
    ) -> DirectoryRecord:
        """Store backend-reported metadata without asserting that KV moved here."""
        _require_text("location", location)
        _require_text("worker_generation", worker_generation)
        if reusable_tokens <= 0 or reusable_tokens > prefix_identity.token_count:
            raise ValueError("reusable_tokens must be positive and no larger than token_count")
        if stored_bytes <= 0:
            raise ValueError("stored_bytes must be positive")
        current_generation = self._worker_generations.get(location)
        if current_generation is None:
            raise ValueError("worker generation must be observed before recording metadata")
        if current_generation != worker_generation:
            raise ValueError("worker_generation does not match the observed worker")

        now = self._clock()
        key = (prefix_identity, location)
        record = DirectoryRecord(
            prefix_identity=prefix_identity,
            location=location,
            worker_generation=worker_generation,
            reusable_tokens=reusable_tokens,
            stored_bytes=stored_bytes,
            complete=complete,
            observed_at=now,
            last_accessed_at=now,
        )
        self._records[key] = record
        self._records.move_to_end(key)
        while len(self._records) > self.capacity:
            self._records.popitem(last=False)
            self.evictions["capacity"] += 1
        return record

    def lookup(
        self,
        prefix_identity: PrefixIdentity,
        destination: str,
        compatibility_namespace: str,
    ) -> LookupResult:
        if compatibility_namespace != prefix_identity.compatibility_namespace:
            return LookupResult(
                LookupOutcome.INCOMPATIBLE,
                prefix_identity,
                destination,
                detail="compatibility_namespace_mismatch",
                metadata_only=True,
            )

        local = self._records.get((prefix_identity, destination))
        if local is not None:
            outcome = self._record_outcome(local)
            if outcome is LookupOutcome.STALE and local.stale_reason is None:
                self._records.pop((prefix_identity, destination), None)
            else:
                self._touch(local)
            return self._lookup_result(local, destination, outcome)

        stale_remote: DirectoryRecord | None = None
        for record in tuple(reversed(self._records.values())):
            if record.prefix_identity != prefix_identity:
                continue
            outcome = self._record_outcome(record)
            if outcome is LookupOutcome.STALE:
                stale_remote = record
                if record.stale_reason is None:
                    self._records.pop((record.prefix_identity, record.location), None)
                continue
            self._touch(record)
            remote_outcome = LookupOutcome.REMOTE if outcome is LookupOutcome.PRESENT else outcome
            return self._lookup_result(record, destination, remote_outcome)

        if stale_remote is not None:
            return self._lookup_result(stale_remote, destination, LookupOutcome.STALE)
        return LookupResult(
            LookupOutcome.MISSING,
            prefix_identity,
            destination,
            metadata_only=True,
        )

    def confirm(self, prefix_identity: PrefixIdentity, destination: str) -> ConfirmResult:
        """Confirm directory freshness only; a backend must prove actual KV availability."""
        record = self._records.get((prefix_identity, destination))
        if record is None:
            return ConfirmResult(
                ConfirmOutcome.MISSING,
                prefix_identity,
                destination,
                metadata_only=True,
            )
        outcome = self._record_outcome(record)
        if outcome is LookupOutcome.STALE:
            detail = record.stale_reason or "ttl_expired"
            if record.stale_reason is None:
                self._records.pop((prefix_identity, destination), None)
            return ConfirmResult(
                ConfirmOutcome.STALE,
                prefix_identity,
                destination,
                detail=detail,
                metadata_only=True,
            )
        self._touch(record)
        if outcome is LookupOutcome.PARTIAL:
            return ConfirmResult(
                ConfirmOutcome.MISSING,
                prefix_identity,
                destination,
                reusable_tokens=record.reusable_tokens,
                available_bytes=record.stored_bytes,
                detail="partial",
                metadata_only=True,
            )
        return ConfirmResult(
            ConfirmOutcome.AVAILABLE,
            prefix_identity,
            destination,
            reusable_tokens=record.reusable_tokens,
            available_bytes=record.stored_bytes,
            metadata_only=True,
        )

    def invalidate(
        self, prefix_identity: PrefixIdentity, location: str, reason: str
    ) -> InvalidateResult:
        _require_text("reason", reason)
        removed = self._records.pop((prefix_identity, location), None)
        return InvalidateResult(
            InvalidateOutcome.INVALIDATED if removed else InvalidateOutcome.MISSING,
            prefix_identity,
            location,
            reason,
        )

    def invalidate_namespace(self, compatibility_namespace: str, reason: str) -> int:
        _require_text("compatibility_namespace", compatibility_namespace)
        _require_text("reason", reason)
        return self._mark_stale(
            lambda record: (
                record.prefix_identity.compatibility_namespace == compatibility_namespace
            ),
            reason=reason,
        )

    def _mark_stale(self, predicate: Callable[[DirectoryRecord], bool], *, reason: str) -> int:
        count = 0
        for key, record in tuple(self._records.items()):
            if predicate(record) and record.stale_reason is None:
                self._records[key] = replace(record, stale_reason=reason)
                count += 1
        return count

    def _record_outcome(self, record: DirectoryRecord) -> LookupOutcome:
        if record.stale_reason is not None:
            return LookupOutcome.STALE
        if self._clock() - record.observed_at >= self.ttl_seconds:
            self.evictions["ttl"] += 1
            return LookupOutcome.STALE
        if self._worker_generations.get(record.location) != record.worker_generation:
            return LookupOutcome.STALE
        if not record.complete:
            return LookupOutcome.PARTIAL
        return LookupOutcome.PRESENT

    def _touch(self, record: DirectoryRecord) -> None:
        key = (record.prefix_identity, record.location)
        current = self._records.get(key)
        if current is None:
            return
        self._records[key] = replace(current, last_accessed_at=self._clock())
        self._records.move_to_end(key)

    @staticmethod
    def _lookup_result(
        record: DirectoryRecord, destination: str, outcome: LookupOutcome
    ) -> LookupResult:
        detail = record.stale_reason
        if outcome is LookupOutcome.STALE and detail is None:
            detail = "ttl_expired"
        return LookupResult(
            outcome,
            record.prefix_identity,
            destination,
            source_or_store=record.location,
            reusable_tokens=record.reusable_tokens,
            stored_bytes=record.stored_bytes,
            detail=detail,
            metadata_only=True,
        )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")

"""Per-tenant sliding-window token budget and concurrency cap (#122 slice 2).

Tenants come from ``TENANT_ALLOWLIST`` (comma separated). Unknown or missing tenants share
one ``other`` bucket, which is also the only non-allowlisted value a metric label may take.
Over quota is a local 429 (``tenant_tokens`` / ``tenant_concurrency``) and never overflows.
"""

from __future__ import annotations

import math
import os
from collections import deque
from dataclasses import dataclass, field

try:  # package import or flat import (ConfigMap-mounted gateway)
    from .admission import Shed
except ImportError:
    from admission import Shed

OTHER = "other"


@dataclass
class _Bucket:
    window: deque = field(default_factory=deque)  # (timestamp, tokens)
    tokens: int = 0
    active: int = 0


class Lease:
    """Concurrency slot; ``release`` is idempotent (safe from error and stream-end paths).

    ``refund=True`` also returns the committed tokens to the window, for work that never
    ran on a GPU (shed after the quota check, or a worker failure)."""

    def __init__(self, bucket: _Bucket, entry: tuple[float, int]) -> None:
        self._bucket: _Bucket | None = bucket
        self._entry = entry

    def release(self, refund: bool = False) -> None:
        b = self._bucket
        if b is None:
            return
        b.active -= 1
        self._bucket = None
        if refund and self._entry in b.window:  # not already aged out of the window
            b.window.remove(self._entry)
            b.tokens -= self._entry[1]


class TenantQuota:
    def __init__(
        self,
        allowlist: frozenset[str] = frozenset(),
        token_budget: int = 200_000,
        window_s: float = 60.0,
        max_concurrency: int = 4,
    ) -> None:
        self.allowlist, self.token_budget = allowlist, token_budget
        self.window_s, self.max_concurrency = window_s, max_concurrency
        self._buckets: dict[str, _Bucket] = {}

    @classmethod
    def from_env(cls) -> TenantQuota:
        names = frozenset(
            t.strip() for t in os.getenv("TENANT_ALLOWLIST", "").split(",") if t.strip()
        )
        return cls(
            names,
            int(os.getenv("TENANT_TOKEN_BUDGET", "200000")),
            float(os.getenv("TENANT_WINDOW_S", "60")),
            int(os.getenv("TENANT_MAX_CONCURRENCY", "4")),
        )

    def bucket_name(self, tenant_id: str | None) -> str:
        return tenant_id if tenant_id in self.allowlist else OTHER

    def acquire(self, tenant_id: str | None, tokens: int, now: float) -> Lease | Shed:
        name = self.bucket_name(tenant_id)
        b = self._buckets.setdefault(name, _Bucket())
        while b.window and b.window[0][0] <= now - self.window_s:
            b.tokens -= b.window.popleft()[1]
        inputs = {"tenant_bucket": name, "window_tokens": b.tokens, "active": b.active}
        if b.active >= self.max_concurrency:
            return Shed(429, "tenant_concurrency", 1, inputs, never_overflow=True)
        if b.tokens + tokens > self.token_budget:
            wait = math.ceil(b.window[0][0] + self.window_s - now) if b.window else 1
            return Shed(429, "tenant_tokens", max(wait, 1), inputs, never_overflow=True)
        entry = (now, tokens)
        b.window.append(entry)
        b.tokens += tokens
        b.active += 1
        return Lease(b, entry)

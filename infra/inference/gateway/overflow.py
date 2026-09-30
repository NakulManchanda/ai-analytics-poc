"""Overflow policy (#122 slice 4): when a local 503/529 may go to a configured destination.

Pure ``decide`` plus env config. Eligible: 503 (local capacity unavailable) and 529 (overload),
unless the shed is ``never_overflow``. Stays local: 429 tenant/rate, 500/502 app/internal,
504 deadline_unachievable, guard 4xx, slice_oom and local config errors (unknown_* reasons).
The API key is read from OVERFLOW_API_KEY only and is never logged or returned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ELIGIBLE_CODES = frozenset({503, 529})
LOCAL_ONLY_REASONS = frozenset(  # 503s that are local bugs/config, not capacity
    {"slice_oom", "unknown_forced_worker", "unknown_policy"}
)


@dataclass(frozen=True)
class Local:
    pass


@dataclass(frozen=True)
class Overflow:
    original_reason: str
    original_code: int


def decide(code: int, reason: str, never_overflow: bool, source: str = "") -> Local | Overflow:
    """``source`` (admit|place|queue|upstream) is informational for callers/logs."""
    if never_overflow or code not in ELIGIBLE_CODES or reason in LOCAL_ONLY_REASONS:
        return Local()
    return Overflow(reason, code)


@dataclass(frozen=True)
class OverflowConfig:
    enabled: bool = False
    provider: str = ""
    model: str = ""
    url: str = ""
    api_key: str = ""
    timeout_s: float = 60.0

    @property
    def active(self) -> bool:  # disabled or misconfigured -> behave exactly as before
        return self.enabled and bool(self.provider and self.model and self.url)

    @property
    def destination(self) -> str:
        return f"{self.provider}/{self.model}"

    @classmethod
    def from_env(cls) -> OverflowConfig:
        g = os.getenv
        return cls(
            g("OVERFLOW_ENABLED", "").lower() in ("1", "true", "yes"),
            g("OVERFLOW_PROVIDER", "").strip(),
            g("OVERFLOW_MODEL", "").strip(),
            g("OVERFLOW_URL", "").strip(),
            g("OVERFLOW_API_KEY", ""),
            float(g("OVERFLOW_TIMEOUT_S", "60")),
        )

    def headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

"""Admission: decide whether the fleet should accept valid work *now* (#122 slice 2).

Pure: no I/O; snapshots and the clock come in as arguments. Shed taxonomy (stable
``orch_shed_total{reason}`` label values), evaluated in this order:

  no_signal            503  no healthy worker with a fresh snapshot (fail closed)
  kv_pressure          503  best eligible worker kv_free_ratio < KV_FREE_MIN
  decode_slots         503  running >= MAX_DECODE_SLOTS on every eligible worker; batch work is
                            shed earlier: it may only use MAX_DECODE_SLOTS minus BATCH_SLOT_RESERVE
                            (a fraction), so the reserved slots stay free for interactive requests
  deadline_unachievable 504 est. queue wait + prefill time > remaining x-deadline-ms
  tenant_tokens / tenant_concurrency  429  (produced by tenants.py; local, never overflow)

Deadline choice: 504 (the request's own time budget cannot be met; retrying the same
deadline is pointless) while capacity shed is 503 (retry later / overflow-eligible, slice 4).
Only 429 is flagged ``never_overflow``. Every result carries the exact snapshot inputs used.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

try:  # package import or flat import (ConfigMap-mounted gateway)
    from .workers import WorkerSnapshot
except ImportError:
    from workers import WorkerSnapshot

SHED_REASONS = (
    "no_signal",
    "kv_pressure",
    "decode_slots",
    "deadline_unachievable",
    "tenant_tokens",
    "tenant_concurrency",
)


@dataclass(frozen=True)
class AdmitConfig:
    max_decode_slots: int = 32
    kv_free_min: float = 0.10
    prefill_tokens_per_s: float = 4000.0
    queue_wait_per_waiting_s: float = 0.25  # est. added wait per request queued at the worker
    stale_after_s: float = 5.0
    retry_after_s: int = 1
    batch_reserve: float = 0.25  # fraction of decode slots kept free of batch work

    @classmethod
    def from_env(cls) -> AdmitConfig:
        d = cls()
        return cls(
            int(os.getenv("MAX_DECODE_SLOTS", d.max_decode_slots)),
            float(os.getenv("KV_FREE_MIN", d.kv_free_min)),
            float(os.getenv("PREFILL_TOKENS_PER_S", d.prefill_tokens_per_s)),
            float(os.getenv("QUEUE_WAIT_PER_WAITING_S", d.queue_wait_per_waiting_s)),
            float(os.getenv("SNAPSHOT_STALE_S", d.stale_after_s)),
            int(os.getenv("SHED_RETRY_AFTER_S", d.retry_after_s)),
            float(os.getenv("BATCH_SLOT_RESERVE", d.batch_reserve)),
        )

    def slot_limit(self, workload_class: str) -> int:
        """Slots a request of this class may count on: batch gives up the reserved fraction."""
        if workload_class != "batch":
            return self.max_decode_slots
        reserved = math.ceil(self.max_decode_slots * min(max(self.batch_reserve, 0.0), 1.0))
        return max(1, self.max_decode_slots - reserved)


@dataclass(frozen=True)
class AdmitRequest:
    est_tokens: int = 0
    deadline_ms: int | None = None
    workload_class: str = "interactive"


@dataclass(frozen=True)
class Admit:
    inputs: dict = field(default_factory=dict)
    shed: bool = False


@dataclass(frozen=True)
class Shed:
    code: int
    reason: str
    retry_after_seconds: int
    inputs: dict = field(default_factory=dict)
    never_overflow: bool = False  # True for 429 (tenant/rate budget): stays local (slice 4)
    shed: bool = True


def _inputs(req: AdmitRequest, snaps: Sequence[WorkerSnapshot], now: float) -> dict:
    return {
        "class": req.workload_class,
        "est_tokens": req.est_tokens,
        "deadline_ms": req.deadline_ms,
        "workers": [
            {
                "id": s.id,
                "healthy": s.healthy,
                "age_s": round(s.age(now), 3) if s.observed_at is not None else None,
                "running": s.running,
                "waiting": s.waiting,
                "kv_free_ratio": s.kv_free_ratio,
                "inflight_tokens": s.inflight_tokens,
            }
            for s in snaps
        ],
    }


def should_shed(
    req: AdmitRequest,
    snapshots: Sequence[WorkerSnapshot],
    *,
    now: float,
    cfg: AdmitConfig,
) -> Admit | Shed:
    inputs = _inputs(req, snapshots, now)
    ra = cfg.retry_after_s
    eligible = [s for s in snapshots if s.healthy and not s.is_stale(cfg.stale_after_s, now)]
    if not eligible:
        return Shed(503, "no_signal", ra, inputs)
    if max(s.kv_free_ratio for s in eligible) < cfg.kv_free_min:
        return Shed(503, "kv_pressure", ra, inputs)
    if all(s.running >= cfg.slot_limit(req.workload_class) for s in eligible):
        return Shed(503, "decode_slots", ra, inputs)
    if req.deadline_ms is not None:
        wait = min(s.waiting for s in eligible) * cfg.queue_wait_per_waiting_s
        estimate = wait + req.est_tokens / max(cfg.prefill_tokens_per_s, 1.0)
        if estimate > req.deadline_ms / 1000:
            inputs["estimated_s"] = round(estimate, 3)
            return Shed(504, "deadline_unachievable", max(ra, math.ceil(wait)), inputs)
    return Admit(inputs)

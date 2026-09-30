"""Bounded-cardinality Prometheus metrics for the gateway (#122 slice 1).

High-cardinality values (request/conversation/prefix ids) go to the decision log only.
"""

from __future__ import annotations

import contextlib
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()
HEALTH_STATES = ("healthy", "unhealthy", "stale")

REQUESTS = Counter(
    "gateway_requests_total", "Gateway requests", ["status", "class"], registry=REGISTRY
)
GUARD_REJECT = Counter("guard_reject_total", "Guard rejections", ["reason"], registry=REGISTRY)
PICKS = Counter(
    "orch_pick_total",
    "Placement picks",
    ["policy", "worker", "reason"],
    registry=REGISTRY,
)
PLACEMENT_ERRORS = Counter(
    "placement_error_total", "Placement errors", ["reason"], registry=REGISTRY
)
WORKER_HEALTH = Gauge(
    "worker_health",
    "1 for the worker's current state",
    ["worker", "state"],
    registry=REGISTRY,
)
SNAPSHOT_AGE = Gauge(
    "worker_snapshot_age_seconds",
    "Age of last good snapshot",
    ["worker"],
    registry=REGISTRY,
)


STALE_FALLBACK = Counter(
    "stale_snapshot_fallback_total",
    "Placements made on stale snapshots",
    registry=REGISTRY,
)
ADMIT = Counter(
    "orch_admit_total",
    "Admission decisions",
    ["decision", "reason", "class"],
    registry=REGISTRY,
)
SHED = Counter("orch_shed_total", "Shed requests", ["reason", "class", "code"], registry=REGISTRY)
TENANT_REQUESTS = Counter(
    "orch_tenant_total",
    "Tenant outcomes (tenant is an allowlisted bucket or 'other')",
    ["tenant", "outcome"],
    registry=REGISTRY,
)
STAGE_DURATION = Histogram(
    "gateway_request_duration_seconds",
    "Time spent per gateway pipeline stage",
    ["stage", "class"],
    registry=REGISTRY,
)


@contextlib.contextmanager
def timed(stage: str, klass: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        STAGE_DURATION.labels(stage, klass).observe(time.perf_counter() - start)


def observe_snapshots(snaps, stale_after: float) -> None:
    for s in snaps:
        state = "unhealthy" if not s.healthy else "stale" if s.is_stale(stale_after) else "healthy"
        for st in HEALTH_STATES:
            WORKER_HEALTH.labels(s.id, st).set(1 if st == state else 0)
        SNAPSHOT_AGE.labels(s.id).set(min(s.age(), 1e9))


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

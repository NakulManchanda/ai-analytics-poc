"""Bounded-cardinality Prometheus metrics for the gateway (#122 slice 1).

High-cardinality values (request/conversation/prefix ids) go to the decision log only.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

REGISTRY = CollectorRegistry()
HEALTH_STATES = ("healthy", "unhealthy", "stale")

REQUESTS = Counter(
    "gateway_requests_total", "Gateway requests", ["status", "class"], registry=REGISTRY
)
GUARD_REJECT = Counter("guard_reject_total", "Guard rejections", ["reason"], registry=REGISTRY)
PICKS = Counter(
    "orch_pick_total", "Placement picks", ["policy", "worker", "reason"], registry=REGISTRY
)
PLACEMENT_ERRORS = Counter(
    "placement_error_total", "Placement errors", ["reason"], registry=REGISTRY
)
WORKER_HEALTH = Gauge(
    "worker_health", "1 for the worker's current state", ["worker", "state"], registry=REGISTRY
)
SNAPSHOT_AGE = Gauge(
    "worker_snapshot_age_seconds", "Age of last good snapshot", ["worker"], registry=REGISTRY
)


STALE_FALLBACK = Counter(
    "stale_snapshot_fallback_total", "Placements made on stale snapshots", registry=REGISTRY
)


def observe_snapshots(snaps, stale_after: float) -> None:
    for s in snaps:
        state = "unhealthy" if not s.healthy else "stale" if s.is_stale(stale_after) else "healthy"
        for st in HEALTH_STATES:
            WORKER_HEALTH.labels(s.id, st).set(1 if st == state else 0)
        SNAPSHOT_AGE.labels(s.id).set(min(s.age(), 1e9))


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

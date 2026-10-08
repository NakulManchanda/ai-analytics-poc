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
WORKER_WARM = Gauge(
    "worker_warm",
    "1 once the worker passed the warm gate (N healthy scrapes + a probe request); healthy but cold = 0",
    ["worker"],
    registry=REGISTRY,
)

WARM_PROBE = Counter(
    "warm_probe_total",
    "Warm-up probe requests sent to a worker by the gateway",
    ["worker", "result"],
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
QUEUE_DEPTH = Gauge(
    "orch_replica_queue_depth",
    "Requests waiting in the gateway queue per worker and class",
    ["worker", "class"],
    registry=REGISTRY,
)
QUEUE_WAIT = Histogram(
    "orch_queue_wait_seconds",
    "Time spent waiting for a dispatch slot",
    ["worker", "class"],
    registry=REGISTRY,
)
QUEUE_ERRORS = Counter(
    "queue_error_total", "Queue rejections", ["reason", "class"], registry=REGISTRY
)
OVERFLOW = Counter(
    "orch_overflow_total",
    "Overflow attempts (reason = original local reason; provider/model from config)",
    ["reason", "provider", "model", "outcome"],
    registry=REGISTRY,
)
OVERFLOW_ERROR = Counter(
    "overflow_error_total", "Overflow attempts that failed", ["reason"], registry=REGISTRY
)
# Client-visible time to first token for STREAMING requests only: from gateway arrival to the
# first SSE chunk with non-empty generated content (role-only deltas, keepalives, errors and
# [DONE] do not count). Non-streaming requests are not observed here (they have no first token;
# see gateway_request_duration_seconds). Buckets bracket the 0.1s interactive SLO.
TTFT = Histogram(
    "gateway_ttft_seconds",
    "Gateway-observed time to first generated token (streaming requests only)",
    ["class"],
    buckets=(0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
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
        WORKER_WARM.labels(s.id).set(1 if s.warm else 0)
        SNAPSHOT_AGE.labels(s.id).set(min(s.age(), 1e9))


def observe_queues(queues, worker_ids, classes) -> None:
    for w in worker_ids:
        for c in classes:
            QUEUE_DEPTH.labels(w, c).set(queues.depth(w, c))


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

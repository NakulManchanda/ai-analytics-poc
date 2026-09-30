"""Placement policies (#122 slice 1). Pure: no I/O, all state comes in as arguments.

Eligibility (documented, tested): unhealthy workers are never picked (none healthy ->
PlacementError ``no_healthy_worker`` -> 503). Stale snapshots (older than ``stale_after``)
and saturated workers (kv_free_ratio < ``min_kv_free``) are dropped while a better
candidate remains. If EVERY healthy snapshot is stale the policy runs on the healthy ones
as an explicit conservative fallback, flagged ``fallback="stale_snapshot"`` on the
decision (counted by the gateway). Never-observed snapshots sort last.

intended_action is what the router *intends*, never proof: ``hop`` needs a confirmed
transfer from #133 and is therefore never produced here.
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

try:  # package import or flat import (ConfigMap-mounted gateway)
    from .workers import WorkerSnapshot
except ImportError:
    from workers import WorkerSnapshot

Policy = Literal["round_robin", "least_loaded", "p2c", "prefix_then_load"]
POLICIES = ("round_robin", "least_loaded", "p2c", "prefix_then_load")
PLACEMENT_ERRORS = ("no_healthy_worker", "unknown_forced_worker", "unknown_policy")
STICKY_OVERLAP = 0.8
STICKY_MAX_INFLIGHT_TOKENS = 10_000


@dataclass(frozen=True)
class PlacementRequest:
    prefix_id: str | None = None
    est_tokens: int = 0
    workload_class: str = "interactive"
    forced_worker: str | None = None


@dataclass(frozen=True)
class PlacementDecision:
    chosen_worker: str
    prior_worker: str | None
    placement_policy: str
    placement_reason: str
    estimated_reusable_tokens: int
    intended_action: Literal["local_reuse", "destination_hit", "recompute", "hop"]
    snapshot_age: float
    fallback: str | None = None


@dataclass(frozen=True)
class PlacementError:
    reason: str


def load(s: WorkerSnapshot) -> tuple[bool, float]:
    """Queue depth (waiting, weighted) participates alongside running and in-flight work;
    a never-observed worker sorts last."""
    return (s.observed_at is None, 2 * s.waiting + s.running + s.inflight)


def pick(
    req: PlacementRequest,
    workers: Sequence[WorkerSnapshot],
    *,
    policy: str,
    stale_after: float = 5.0,
    now: float | None = None,
    rr_index: int = 0,
    rng: random.Random | None = None,
    kv_used_max: float = 0.70,
    min_kv_free: float = 0.20,
    allow_forced: bool = False,
) -> PlacementDecision | PlacementError:
    now = time.monotonic() if now is None else now
    by_id = {w.id: w for w in workers}
    prior = _prior_worker(req, workers)

    fallback: str | None = None

    def decide(snap: WorkerSnapshot, pol: str, reason: str) -> PlacementDecision:
        belief = snap.prefixes.get(req.prefix_id) if req.prefix_id else None
        if belief is None:
            action, reusable = "recompute", 0
        else:
            action = "local_reuse" if snap.id == prior else "destination_hit"
            reusable = belief.tokens
        return PlacementDecision(
            snap.id, prior, pol, reason, reusable, action, snap.age(now), fallback
        )

    if req.forced_worker and allow_forced:
        if req.forced_worker not in by_id:
            return PlacementError("unknown_forced_worker")
        return decide(by_id[req.forced_worker], "forced", "forced")

    healthy = [w for w in workers if w.healthy]
    if not healthy:
        return PlacementError("no_healthy_worker")
    eligible = [w for w in healthy if not w.is_stale(stale_after, now)]
    if not eligible:
        eligible, fallback = healthy, "stale_snapshot"
    eligible = [w for w in eligible if w.kv_free_ratio >= min_kv_free] or eligible

    if policy == "round_robin":
        return decide(eligible[rr_index % len(eligible)], policy, "round_robin")
    if policy == "least_loaded":
        return decide(min(eligible, key=load), policy, "least_loaded")
    if policy == "p2c":
        return decide(_p2c(eligible, rng), policy, "p2c")
    if policy == "prefix_then_load":
        owner = by_id.get(prior) if prior else None
        if owner is None:
            return decide(_p2c(eligible, rng), policy, "no_prefix_known")
        if owner not in eligible:
            return decide(_p2c(eligible, rng), policy, "prefix_owner_unavailable")
        overlap = (
            owner.prefixes[req.prefix_id].tokens / req.est_tokens if req.est_tokens > 0 else 1.0
        )
        if 1.0 - owner.kv_free_ratio >= kv_used_max and len(eligible) > 1:
            return decide(
                _p2c([w for w in eligible if w is not owner], rng),
                policy,
                "prefix_owner_kv_pressure",
            )
        if overlap < STICKY_OVERLAP:
            return decide(_p2c(eligible, rng), policy, "prefix_overlap_low")
        if owner.inflight_tokens >= STICKY_MAX_INFLIGHT_TOKENS and len(eligible) > 1:
            return decide(
                _p2c([w for w in eligible if w is not owner], rng), policy, "prefix_owner_busy"
            )
        return decide(owner, policy, "prefix_affinity")
    return PlacementError("unknown_policy")


def _p2c(cands: Sequence[WorkerSnapshot], rng: random.Random | None) -> WorkerSnapshot:
    return min((rng or random).sample(list(cands), min(2, len(cands))), key=load)


def _prior_worker(req: PlacementRequest, workers: Sequence[WorkerSnapshot]) -> str | None:
    """Worker most recently believed to hold this prefix identity (not tenant)."""
    if not req.prefix_id:
        return None
    held = [
        (w.prefixes[req.prefix_id].observed_at, w.id)
        for w in workers
        if req.prefix_id in w.prefixes
    ]
    return max(held)[1] if held else None

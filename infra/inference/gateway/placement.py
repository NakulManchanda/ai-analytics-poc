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
PLACEMENT_ERRORS = (
    "no_healthy_worker",
    "batch_slot_cap",
    "unknown_forced_worker",
    "unknown_policy",
)
STICKY_MAX_INFLIGHT_TOKENS = 10_000
SPILL_QUEUE = 4  # waiting + gateway-queued + just-placed requests that make an owner "saturated"


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
    prior_reusable_tokens: int = 0  # tokens the prior owner is believed to hold (hop size)


@dataclass(frozen=True)
class PlacementError:
    reason: str


def load(s: WorkerSnapshot) -> tuple[bool, float]:
    """Worker queue depth (waiting, weighted), the gateway's own queue (queued) and requests placed
    but not yet queued (pending) participate alongside running and in-flight work;
    a never-observed worker sorts last."""
    return (s.observed_at is None, 2 * s.waiting + s.running + s.inflight + s.queued + s.pending)


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
    spill_queue: int = SPILL_QUEUE,
    batch_slot_limit: int | None = None,
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
        owner = by_id.get(prior) if prior else None
        owner_belief = owner.prefixes.get(req.prefix_id) if owner and req.prefix_id else None
        return PlacementDecision(
            snap.id,
            prior,
            pol,
            reason,
            reusable,
            action,
            snap.age(now),
            fallback,
            owner_belief.tokens if owner_belief else 0,
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
    if req.workload_class == "batch" and batch_slot_limit is not None:
        # Batch may only use slots below its cap, on the worker actually chosen (admission only
        # checks that SOME worker has room); the reserved slots stay free for interactive work.
        eligible = [w for w in eligible if w.occupied < batch_slot_limit]
        if not eligible:
            return PlacementError("batch_slot_cap")

    if policy == "round_robin":
        return decide(eligible[rr_index % len(eligible)], policy, "round_robin")
    if policy == "least_loaded":
        best = min(load(w) for w in eligible)
        ties = [w for w in eligible if load(w) == best]
        return decide((rng or random).choice(ties), policy, "least_loaded")
    if policy == "p2c":
        return decide(_p2c(eligible, rng), policy, "p2c")
    if policy == "prefix_then_load":
        owner = by_id.get(prior) if prior else None
        if owner is None:
            return decide(_p2c(eligible, rng), policy, "no_prefix_known")
        if owner not in eligible:
            return decide(_p2c(eligible, rng), policy, "prefix_owner_unavailable")
        # Sticky owner: a conversation that has a KV owner stays there, however much its prompt has
        # grown. It moves only when the owner is saturated (KV, in-flight tokens, or queue) and
        # another eligible worker can take it; a queue-only saturation also needs that worker to be
        # less loaded, otherwise moving would only throw the KV away.
        others = [w for w in eligible if w is not owner]
        why = _owner_saturated(owner, kv_used_max, spill_queue)
        if others and why:
            target = min(others, key=load)
            if why != "prefix_owner_saturated" or load(target) < load(owner):
                return decide(target, policy, why)
        return decide(owner, policy, "prefix_affinity")
    return PlacementError("unknown_policy")


def _owner_saturated(owner: WorkerSnapshot, kv_used_max: float, spill_queue: int) -> str | None:
    """Reason the prefix owner should be left, or None when the conversation should stay."""
    if 1.0 - owner.kv_free_ratio >= kv_used_max:
        return "prefix_owner_kv_pressure"
    if owner.inflight_tokens >= STICKY_MAX_INFLIGHT_TOKENS:
        return "prefix_owner_busy"
    if owner.waiting + owner.queued + owner.pending >= spill_queue:
        return "prefix_owner_saturated"
    return None


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

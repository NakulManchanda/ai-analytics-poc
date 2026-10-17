"""Worker registry and placement snapshots (#122 slice 1).

Snapshot signals (all scraped from the worker's vLLM /metrics):
  running / waiting  vllm:num_requests_running / vllm:num_requests_waiting
  kv_free_ratio      1 - vllm:kv_cache_usage_perc (0..1 fraction used)
  healthy            last scrape succeeded
  warm               with the warm gate off: at least one scrape ever succeeded (legacy). With it on
                     (WARM_GATE=1): N consecutive healthy scrapes AND one successful probe request;
                     any failed scrape makes the worker cold again (it may have restarted)
  observed_at        monotonic time of the last successful scrape
  prefixes           the router's time-bounded *belief* of where a prefix identity
                     was last placed (not proof of a KV hit; vLLM may have evicted it)
  inflight[_tokens]  requests / estimated prompt tokens dispatched by this gateway, not yet done
  queued             requests admitted and placed here but still waiting for a dispatch slot
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

import httpx
from prometheus_client.parser import text_string_to_metric_families

log = logging.getLogger("gateway.workers")

DEFAULT_A_URL = "http://inference-worker-a.inference-lab.svc.cluster.local:8000"


@dataclass(frozen=True)
class Worker:
    id: str
    url: str


class PrefixBelief(NamedTuple):
    observed_at: float
    tokens: int


@dataclass
class WorkerSnapshot:
    worker: Worker
    observed_at: float | None = None
    running: int = 0
    waiting: int = 0
    kv_free_ratio: float = 1.0
    healthy: bool = False
    warm: bool = False
    healthy_streak: int = 0  # consecutive successful scrapes (warm gate)
    health_epoch: int = 0  # bumped on every failed scrape; a probe only counts within its epoch
    warmed_at: float | None = None
    warm_count: int = 0  # times this worker has passed the warm gate (2+ means it returned)
    requests_since_warm: int = 0  # requests dispatched here since it last became warm
    ramp_cap: int | None = None  # max in-flight + queued while ramping a returning worker
    ramp_stage: int = 0
    ramp_next_at: float = 0.0
    inflight: int = 0
    inflight_tokens: int = 0
    queued: int = 0  # requests waiting in this gateway's per-worker queue (#122 slice 3)
    pending: int = 0  # requests placed here that have not reached the queue yet (anti-herding)
    dispatch_grace_s: float = 2.0  # how long a scrape may lag a dispatch (see ``occupied``)
    recent_dispatches: dict[int, float] = field(default_factory=dict)  # token -> monotonic time
    _dispatch_id: int = 0
    prefixes: dict[str, PrefixBelief] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.worker.id

    @property
    def occupied(self) -> int:
        """Decode slots in use, as a safe upper bound.

        The last scrape's ``running`` plus every dispatch of ours that is still running and was
        made within ``dispatch_grace_s`` of that scrape (or after it). A scrape cannot be shown to
        include a request vLLM has not scheduled yet, so a dispatch is only dropped once a scrape
        was taken a full grace window after it. Inside the window a request the scrape already
        includes is counted twice: the error is on the safe side (batch gets slightly less).
        """
        horizon = float("-inf") if self.observed_at is None else self.observed_at
        return self.running + sum(
            1 for dispatched_at in self.recent_dispatches.values()
            if dispatched_at > horizon - self.dispatch_grace_s
        )

    def note_dispatch(self, now: float | None = None) -> int:
        """Count a dispatch; returns the token to pass to ``note_finish``."""
        self._dispatch_id += 1
        self.recent_dispatches[self._dispatch_id] = time.monotonic() if now is None else now
        return self._dispatch_id

    def warm_context(self, now: float | None = None) -> tuple[int, int]:
        """(ms since the worker became warm or -1 if never, request index since warm) and count it.

        Lets a slow first step be told apart from hop cost: index 0 is the first request this
        replica served after it became warm.
        """
        index = self.requests_since_warm
        self.requests_since_warm += 1
        if self.warmed_at is None:
            return -1, index
        return int(((time.monotonic() if now is None else now) - self.warmed_at) * 1000), index

    def note_finish(self, token: int) -> None:
        self.recent_dispatches.pop(token, None)

    def age(self, now: float | None = None) -> float:
        if self.observed_at is None:
            return float("inf")
        return (time.monotonic() if now is None else now) - self.observed_at

    def is_stale(self, threshold: float, now: float | None = None) -> bool:
        return self.age(now) > threshold


def parse_vllm_metrics(text: str) -> dict[str, float]:
    """Extract the gauges placement needs from Prometheus text."""
    wanted = {
        "vllm:num_requests_running": "running",
        "vllm:num_requests_waiting": "waiting",
        "vllm:kv_cache_usage_perc": "kv_used",
    }
    out: dict[str, float] = {}
    for family in text_string_to_metric_families(text):
        if family.name in wanted and family.samples:
            out[wanted[family.name]] = family.samples[0].value
    return out


class Registry:
    def __init__(
        self,
        workers: list[Worker],
        *,
        require_warm: bool = False,
        warm_min_scrapes: int = 3,
        probe_model: str = "Qwen/Qwen3-0.6B",
        probe_timeout_s: float = 30.0,
        ramp_steps: tuple[int, ...] = (),
        ramp_step_s: float = 30.0,
        ramp_min_kv_free: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        dispatch_grace_s: float = 2.0,
    ) -> None:
        self.snapshots = {
            w.id: WorkerSnapshot(w, dispatch_grace_s=dispatch_grace_s) for w in workers
        }
        self._rr = 0
        self.require_warm = require_warm
        self.warm_min_scrapes = max(1, warm_min_scrapes)
        self.probe_model = probe_model
        self.probe_timeout_s = probe_timeout_s
        self._probes: dict[str, asyncio.Task] = {}
        self.ramp_steps = ramp_steps
        self.ramp_step_s = ramp_step_s
        self.ramp_min_kv_free = ramp_min_kv_free
        self._clock = clock
        self.on_probe = None  # optional callback(worker_id, result) for metrics

    def mark_cold(self, worker_id: str, reason: str) -> None:
        """Make a worker requalify (scrapes + probe) after it reported a fault such as an OOM."""
        snap = self.snapshots[worker_id]
        snap.healthy_streak = 0
        snap.health_epoch += 1  # a probe in flight must not warm it again
        if self.require_warm and snap.warm:
            snap.warm, snap.warmed_at, snap.ramp_cap = False, None, None
            log.info("worker %s marked cold (%s)", worker_id, reason)

    def next_rr(self) -> int:
        self._rr += 1
        return self._rr - 1

    def serving_snapshots(self) -> list[WorkerSnapshot]:
        """Snapshots placement and admission may use.

        Warm workers only when the warm gate is on. A returning worker that is still ramping is
        left out once its in-flight + queued work reaches its cap, as long as another warm worker
        remains; if every warm worker is capped out the cap is ignored rather than shedding.
        """
        warm = [s for s in self.snapshots.values() if not self.require_warm or s.warm]
        if not self.ramp_steps:
            return warm
        open_ = [s for s in warm if s.ramp_cap is None or s.inflight + s.queued < s.ramp_cap]
        return open_ or warm

    async def refresh(self, client: httpx.AsyncClient) -> None:
        await asyncio.gather(*(self._scrape(client, s) for s in self.snapshots.values()))

    async def _scrape(self, client: httpx.AsyncClient, snap: WorkerSnapshot) -> None:
        try:
            resp = await client.get(f"{snap.worker.url}/metrics")
            resp.raise_for_status()
            vals = parse_vllm_metrics(resp.text)
            if "running" not in vals or "waiting" not in vals or "kv_used" not in vals:
                raise ValueError("vLLM gauges missing from /metrics")
        except (httpx.HTTPError, ValueError):
            snap.healthy = False  # keep last numbers; observed_at is not advanced -> goes stale
            snap.healthy_streak = 0
            snap.health_epoch += 1
            if self.require_warm and snap.warm:
                snap.warm, snap.warmed_at = False, None
                log.info("worker %s went cold (scrape failed)", snap.id)
            snap.ramp_cap = None
            return
        snap.running, snap.waiting = int(vals["running"]), int(vals["waiting"])
        snap.kv_free_ratio = max(0.0, 1.0 - vals["kv_used"])
        snap.observed_at = time.monotonic()
        snap.healthy = True
        snap.healthy_streak += 1
        self._advance_ramp(snap)
        if not self.require_warm:
            if not snap.warm:  # legacy: one good scrape
                snap.warm, snap.warmed_at, snap.requests_since_warm = True, time.monotonic(), 0
        elif not snap.warm and snap.healthy_streak >= self.warm_min_scrapes:
            self._start_probe(client, snap)

    def _start_ramp(self, snap: WorkerSnapshot) -> None:
        """A worker returning after being cold starts under a cap; its first warm-up does not."""
        if self.ramp_steps and snap.warm_count > 1:
            snap.ramp_stage, snap.ramp_cap = 0, self.ramp_steps[0]
            snap.ramp_next_at = self._clock() + self.ramp_step_s
            log.info("worker %s ramping, cap=%d", snap.id, snap.ramp_cap)

    def _advance_ramp(self, snap: WorkerSnapshot) -> None:
        """Raise the cap one step per interval while the worker is not under pressure."""
        if snap.ramp_cap is None or self._clock() < snap.ramp_next_at:
            return
        snap.ramp_next_at = self._clock() + self.ramp_step_s
        if snap.waiting > 0 or snap.kv_free_ratio < self.ramp_min_kv_free:
            log.info("worker %s ramp held at cap=%d (under pressure)", snap.id, snap.ramp_cap)
            return
        snap.ramp_stage += 1
        if snap.ramp_stage >= len(self.ramp_steps):
            snap.ramp_cap = None
            log.info("worker %s ramp complete", snap.id)
        else:
            snap.ramp_cap = self.ramp_steps[snap.ramp_stage]
            log.info("worker %s ramp cap raised to %d", snap.id, snap.ramp_cap)

    def _start_probe(self, client: httpx.AsyncClient, snap: WorkerSnapshot) -> None:
        """Probe in the background so a slow first request cannot make other snapshots go stale."""
        task = self._probes.get(snap.id)
        if task is not None and not task.done():
            return
        self._probes[snap.id] = asyncio.create_task(self._probe(client, snap))

    async def _probe(self, client: httpx.AsyncClient, snap: WorkerSnapshot) -> None:
        """One tiny completion straight to the worker: the first request pays the cold cost here."""
        result = "ok"
        epoch = snap.health_epoch
        try:
            resp = await client.post(
                f"{snap.worker.url}/v1/chat/completions",
                json={
                    "model": self.probe_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                },
                timeout=self.probe_timeout_s,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            result = "error"
        if result == "ok" and (
            not snap.healthy
            or snap.health_epoch != epoch
            or snap.healthy_streak < self.warm_min_scrapes
        ):
            # An outage happened while the probe was in flight: the worker must re-qualify.
            result = "stale"
        if result == "ok":
            snap.warm, snap.warmed_at, snap.requests_since_warm = True, time.monotonic(), 0
            snap.warm_count += 1
            self._start_ramp(snap)
            log.info("worker %s is warm (scrapes=%d, probe ok)", snap.id, snap.healthy_streak)
        if self.on_probe is not None:
            self.on_probe(snap.id, result)

    def record_prefix(self, worker_id: str, prefix_id: str | None, tokens: int) -> None:
        if prefix_id:
            self.snapshots[worker_id].prefixes[prefix_id] = PrefixBelief(time.monotonic(), tokens)


def build_registry() -> Registry:
    """WORKER_A_URL (falls back to DEFAULT_WORKER_URL) and optional WORKER_B_URL.

    WARM_GATE=1 enables the warm gate (WARM_MIN_SCRAPES, WARM_PROBE_MODEL); WARM_RAMP_STEPS
    ("2,4") caps a returning worker and raises the cap every WARM_RAMP_STEP_S while it is not
    under pressure. Both are off by default so local runs behave as before.
    """
    a_url = os.getenv("WORKER_A_URL") or os.getenv("DEFAULT_WORKER_URL") or DEFAULT_A_URL
    workers = [Worker("worker_a", a_url.rstrip("/"))]
    if b_url := os.getenv("WORKER_B_URL"):
        workers.append(Worker("worker_b", b_url.rstrip("/")))
    return Registry(
        workers,
        require_warm=os.getenv("WARM_GATE", "0") == "1",
        warm_min_scrapes=int(os.getenv("WARM_MIN_SCRAPES", "3")),
        probe_model=os.getenv("WARM_PROBE_MODEL", "Qwen/Qwen3-0.6B"),
        ramp_steps=tuple(int(x) for x in os.getenv("WARM_RAMP_STEPS", "").split(",") if x.strip()),
        ramp_step_s=float(os.getenv("WARM_RAMP_STEP_S", "30")),
        dispatch_grace_s=float(os.getenv("DISPATCH_GRACE_S", "2")),
    )

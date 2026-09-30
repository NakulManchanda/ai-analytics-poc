"""Worker registry and placement snapshots (#122 slice 1).

Snapshot signals (all scraped from the worker's vLLM /metrics):
  running / waiting  vllm:num_requests_running / vllm:num_requests_waiting
  kv_free_ratio      1 - vllm:kv_cache_usage_perc (0..1 fraction used)
  healthy / warm     last scrape succeeded / at least one scrape ever succeeded
  observed_at        monotonic time of the last successful scrape
  prefixes           the router's time-bounded *belief* of where a prefix identity
                     was last placed (not proof of a KV hit; vLLM may have evicted it)
  inflight[_tokens]  requests / estimated prompt tokens dispatched by this gateway, not yet done
  queued             requests admitted and placed here but still waiting for a dispatch slot
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import NamedTuple

import httpx
from prometheus_client.parser import text_string_to_metric_families

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
    inflight: int = 0
    inflight_tokens: int = 0
    queued: int = 0  # requests waiting in this gateway's per-worker queue (#122 slice 3)
    prefixes: dict[str, PrefixBelief] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.worker.id

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
    def __init__(self, workers: list[Worker]) -> None:
        self.snapshots = {w.id: WorkerSnapshot(w) for w in workers}
        self._rr = 0

    def next_rr(self) -> int:
        self._rr += 1
        return self._rr - 1

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
            return
        snap.running, snap.waiting = int(vals["running"]), int(vals["waiting"])
        snap.kv_free_ratio = max(0.0, 1.0 - vals["kv_used"])
        snap.observed_at = time.monotonic()
        snap.healthy = snap.warm = True

    def record_prefix(self, worker_id: str, prefix_id: str | None, tokens: int) -> None:
        if prefix_id:
            self.snapshots[worker_id].prefixes[prefix_id] = PrefixBelief(time.monotonic(), tokens)


def build_registry() -> Registry:
    """WORKER_A_URL (falls back to DEFAULT_WORKER_URL) and optional WORKER_B_URL."""
    a_url = os.getenv("WORKER_A_URL") or os.getenv("DEFAULT_WORKER_URL") or DEFAULT_A_URL
    workers = [Worker("worker_a", a_url.rstrip("/"))]
    if b_url := os.getenv("WORKER_B_URL"):
        workers.append(Worker("worker_b", b_url.rstrip("/")))
    return Registry(workers)

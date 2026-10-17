"""Per-worker gateway queues with dispatch slots (#122 slice 3).

After placement a request waits here for one of ``WORKER_MAX_INFLIGHT`` dispatch slots on its
chosen worker; the gateway owns priority BEFORE vLLM (vLLM keeps FCFS/preemption/batching).
Waiting is bounded (``QUEUE_MAX_DEPTH`` -> ``queue_full``) and time-limited (the smaller of
``QUEUE_TIMEOUT_S`` and the request's remaining deadline -> ``timeout_queue``); a rejected
or cancelled waiter is removed and never dispatched.

Dispatch order = min key (adapted from the class-7 EDF queue), evaluated at dispatch time:
  (class rank, long prompt, slack - AGING_GAIN * passed_over, enqueued_at)
interactive before batch, short before long, then least slack. Each dispatch counts as an
overtake for every older waiter it jumps; after ``MAX_OVERTAKES`` a waiter is starved and is
ranked as interactive and short, so batch/long work cannot wait forever. Items without a
deadline use ``enqueued_at + QUEUE_TIMEOUT_S`` as their effective deadline.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

CLASSES = ("interactive", "batch")
REJECT_REASONS = ("queue_full", "timeout_queue")


@dataclass(frozen=True)
class QueueConfig:
    max_depth: int = 64
    max_inflight: int = 32
    timeout_s: float = 10.0
    aging_gain: float = 0.5
    max_overtakes: int = 8
    long_prompt_tokens: int = 2000

    @classmethod
    def from_env(cls) -> QueueConfig:
        d = cls()
        return cls(
            int(os.getenv("QUEUE_MAX_DEPTH", d.max_depth)),
            int(os.getenv("WORKER_MAX_INFLIGHT", d.max_inflight)),
            float(os.getenv("QUEUE_TIMEOUT_S", d.timeout_s)),
            float(os.getenv("AGING_GAIN", d.aging_gain)),
            int(os.getenv("MAX_OVERTAKES", d.max_overtakes)),
            int(os.getenv("LONG_PROMPT_TOKENS", d.long_prompt_tokens)),
        )


class QueueRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class QueueItem:
    workload_class: str
    est_tokens: int
    deadline_at: float  # monotonic
    enqueued_at: float
    passed_over: int = 0
    fut: asyncio.Future | None = field(default=None, repr=False)


class QueueTicket:
    """A held dispatch slot. ``release`` is idempotent."""

    def __init__(self, queues: WorkerQueues, worker_id: str, wait_s: float) -> None:
        self._queues, self.worker_id, self.wait_s, self._done = queues, worker_id, wait_s, False

    def release(self) -> None:
        if not self._done:
            self._done = True
            self._queues._free(self.worker_id)


class WorkerQueues:
    def __init__(self, cfg: QueueConfig | None = None) -> None:
        self.cfg = cfg or QueueConfig()
        self._waiting: dict[str, list[QueueItem]] = {}
        self._inflight: dict[str, int] = {}

    def depth(self, worker_id: str, klass: str | None = None) -> int:
        return sum(
            1
            for i in self._waiting.get(worker_id, ())
            if klass is None or i.workload_class == klass
        )

    def inflight(self, worker_id: str) -> int:
        return self._inflight.get(worker_id, 0)

    def key(self, item: QueueItem, now: float) -> tuple:
        starved = item.passed_over >= self.cfg.max_overtakes
        rank = 0 if starved or item.workload_class != "batch" else 1
        long = 0 if starved or item.est_tokens < self.cfg.long_prompt_tokens else 1
        slack = (item.deadline_at - now) - self.cfg.aging_gain * item.passed_over
        return (rank, long, slack, item.enqueued_at)

    async def acquire(
        self,
        worker_id: str,
        workload_class: str,
        est_tokens: int,
        deadline_at: float | None = None,
    ) -> QueueTicket:
        """Wait for a dispatch slot. Raises QueueRejected; cancellation frees any slot/entry."""
        now = time.monotonic()
        waiting = self._waiting.setdefault(worker_id, [])
        if not waiting and self.inflight(worker_id) < self.cfg.max_inflight:
            self._inflight[worker_id] = self.inflight(worker_id) + 1
            return QueueTicket(self, worker_id, 0.0)
        if len(waiting) >= self.cfg.max_depth:
            raise QueueRejected("queue_full")
        item = QueueItem(
            workload_class,
            est_tokens,
            deadline_at if deadline_at is not None else now + self.cfg.timeout_s,
            now,
            fut=asyncio.get_running_loop().create_future(),
        )
        waiting.append(item)
        timeout = self.cfg.timeout_s
        if deadline_at is not None:
            timeout = min(timeout, deadline_at - now)
        try:
            ticket = await asyncio.wait_for(item.fut, max(timeout, 0.0))
        except TimeoutError:
            self._remove(worker_id, item)
            raise QueueRejected("timeout_queue") from None
        except asyncio.CancelledError:
            if item.fut.done() and not item.fut.cancelled():
                item.fut.result().release()  # granted just as we were cancelled
            self._remove(worker_id, item)
            raise
        ticket.wait_s = time.monotonic() - now
        return ticket

    def _remove(self, worker_id: str, item: QueueItem) -> None:
        if item in self._waiting.get(worker_id, ()):
            self._waiting[worker_id].remove(item)

    def _free(self, worker_id: str) -> None:
        self._inflight[worker_id] -= 1
        self._pump(worker_id)

    def _pump(self, worker_id: str) -> None:
        waiting = self._waiting.get(worker_id, [])
        while waiting and self.inflight(worker_id) < self.cfg.max_inflight:
            now = time.monotonic()
            best = min(waiting, key=lambda i: self.key(i, now))
            waiting.remove(best)
            if best.fut.done():  # timed out / cancelled while queued
                continue
            for other in waiting:
                if other.enqueued_at < best.enqueued_at:
                    other.passed_over += 1
            self._inflight[worker_id] = self.inflight(worker_id) + 1
            best.fut.set_result(QueueTicket(self, worker_id, 0.0))

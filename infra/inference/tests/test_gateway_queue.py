"""Per-worker gateway queue tests (#122 slice 3)."""

import asyncio
import functools
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway import metrics
from infra.inference.gateway.admission import Shed
from infra.inference.gateway.placement import PlacementRequest, pick
from infra.inference.gateway.queueing import QueueConfig, QueueRejected, WorkerQueues
from infra.inference.gateway.tenants import TenantQuota
from infra.inference.gateway.workers import Registry, Worker, WorkerSnapshot

MSG = [{"role": "user", "content": "hello"}]


def cfg(**kw) -> QueueConfig:
    return QueueConfig(**{"max_inflight": 1, "max_depth": 8, "timeout_s": 5.0, **kw})


def arun(fn):
    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper


async def spawn(q, order, name, klass="interactive", tokens=10, wid="w", deadline_at=None):
    async def run():
        t = await q.acquire(wid, klass, tokens, deadline_at)
        order.append(name)
        return t

    task = asyncio.create_task(run())
    await asyncio.sleep(0)  # let it enqueue
    return task


async def run_order(q, specs):
    """Occupy the only slot, queue ``specs``, then release one slot at a time."""
    held = await q.acquire("w", "interactive", 1)
    order: list[str] = []
    tasks = {spec[0]: await spawn(q, order, *spec) for spec in specs}
    for _ in specs:
        held.release()
        await asyncio.sleep(0.01)
        held = tasks[order[-1]].result()
    held.release()
    return order


# --- ordering ---------------------------------------------------------------------------


@arun
async def test_interactive_before_batch() -> None:
    q = WorkerQueues(cfg())
    order = await run_order(q, [("b1", "batch"), ("b2", "batch"), ("i1", "interactive")])
    assert order == ["i1", "b1", "b2"]


@arun
async def test_short_before_long() -> None:
    q = WorkerQueues(cfg(long_prompt_tokens=100))
    order = await run_order(q, [("long", "interactive", 500), ("short", "interactive", 5)])
    assert order == ["short", "long"]


@arun
async def test_least_slack_then_fifo() -> None:
    q = WorkerQueues(cfg())
    now = time.monotonic()
    order = await run_order(
        q,
        [
            ("loose", "interactive", 10, "w", now + 4),
            ("tight", "interactive", 10, "w", now + 1),
            ("also_loose", "interactive", 10, "w", now + 4),
        ],
    )
    assert order == ["tight", "loose", "also_loose"]


@arun
async def test_batch_not_starved_by_interactive_stream() -> None:
    q = WorkerQueues(cfg(max_overtakes=2, max_depth=20))
    first = await q.acquire("w", "interactive", 1)
    order: list[str] = []
    tasks = {"batch": await spawn(q, order, "batch", "batch")}
    tasks |= {f"i{n}": await spawn(q, order, f"i{n}") for n in range(6)}
    held = first
    for _ in tasks:
        held.release()
        await asyncio.sleep(0.01)
        held = tasks[order[-1]].result()
    held.release()
    # two interactive requests overtake it, then it is starved and ranks as interactive
    assert order.index("batch") == 2
    assert order == ["i0", "i1", "batch", "i2", "i3", "i4", "i5"]


# --- bounds, timeout, cancellation --------------------------------------------------------


@arun
async def test_queue_full_is_bounded() -> None:
    q = WorkerQueues(cfg(max_depth=2))
    held = await q.acquire("w", "interactive", 1)
    order: list[str] = []
    waiters = [await spawn(q, order, f"r{n}") for n in range(2)]
    assert q.depth("w") == 2
    with pytest.raises(QueueRejected) as exc:
        await q.acquire("w", "interactive", 1)
    assert exc.value.reason == "queue_full" and q.depth("w") == 2
    for w in waiters:
        w.cancel()
    held.release()


@arun
async def test_timeout_queue_removes_entry_and_never_gets_slot() -> None:
    q = WorkerQueues(cfg(timeout_s=0.05))
    held = await q.acquire("w", "interactive", 1)
    with pytest.raises(QueueRejected) as exc:
        await q.acquire("w", "interactive", 1)
    assert exc.value.reason == "timeout_queue" and q.depth("w") == 0
    held.release()
    assert q.inflight("w") == 0  # the timed-out waiter was not granted a slot


@arun
async def test_remaining_deadline_caps_wait() -> None:
    q = WorkerQueues(cfg(timeout_s=30))
    held = await q.acquire("w", "interactive", 1)
    start = time.monotonic()
    with pytest.raises(QueueRejected, match="timeout_queue"):
        await q.acquire("w", "interactive", 1, deadline_at=start + 0.05)
    assert time.monotonic() - start < 1
    held.release()


@arun
async def test_cancellation_frees_entry_and_slot() -> None:
    q = WorkerQueues(cfg())
    held = await q.acquire("w", "interactive", 1)
    order: list[str] = []
    waiter = await spawn(q, order, "gone")
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert q.depth("w") == 0
    held.release()
    assert q.inflight("w") == 0 and order == []
    t = await q.acquire("w", "interactive", 1)  # slot usable again
    t.release()


@arun
async def test_release_is_idempotent_and_dispatches_next() -> None:
    q = WorkerQueues(cfg())
    held = await q.acquire("w", "interactive", 1)
    order: list[str] = []
    nxt = await spawn(q, order, "next")
    held.release()
    held.release()
    await asyncio.sleep(0.01)
    assert order == ["next"] and q.inflight("w") == 1
    nxt.result().release()
    assert q.inflight("w") == 0


@arun
async def test_workers_have_independent_queues() -> None:
    q = WorkerQueues(cfg())
    a = await q.acquire("a", "interactive", 1)
    b = await q.acquire("b", "interactive", 1)  # not blocked by a
    a.release(), b.release()


# --- placement -----------------------------------------------------------------------------


def snap(wid, queued=0):
    s = WorkerSnapshot(Worker(wid, f"http://{wid}:8000"), time.monotonic(), 0, 0, 1.0, True, True)
    s.queued = queued
    return s


def test_queue_depth_affects_placement() -> None:
    workers = [snap("worker_a", queued=5), snap("worker_b", queued=0)]
    for policy in ("least_loaded", "p2c"):
        d = pick(PlacementRequest(), workers, policy=policy)
        assert d.chosen_worker == "worker_b"


# --- metrics -------------------------------------------------------------------------------


@arun
async def test_metrics_labels_bounded() -> None:
    q = WorkerQueues(cfg())
    held = await q.acquire("worker_a", "batch", 1)
    order: list[str] = []
    w = await spawn(q, order, "x", "batch", wid="worker_a")
    metrics.observe_queues(q, ["worker_a"], ("interactive", "batch"))
    body = metrics.render()[0].decode()
    assert 'orch_replica_queue_depth{class="batch",worker="worker_a"} 1.0' in body
    assert 'orch_replica_queue_depth{class="interactive",worker="worker_a"} 0.0' in body
    w.cancel()
    held.release()


# --- gateway e2e ---------------------------------------------------------------------------


@pytest.fixture
def gw(monkeypatch):
    reg = Registry([Worker("worker_a", "http://worker-a:8000")])
    s = reg.snapshots["worker_a"]
    s.healthy, s.observed_at = True, time.monotonic()
    monkeypatch.setattr(gateway_main, "registry", reg)
    monkeypatch.setattr(gateway_main, "queues", WorkerQueues(cfg(timeout_s=0.05)))
    return TestClient(gateway_main.app), reg


OK = httpx.Response(200, json={"choices": []})


def serve(client, **headers):
    return client.post("/serve", json={"messages": MSG}, headers=headers)


def test_dispatched_headers_and_slot_released(gw) -> None:
    client, reg = gw
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=OK) as post:
        r = serve(client)
    assert r.status_code == 200
    assert r.headers["x-queue-decision"] == "dispatched"
    assert int(r.headers["x-queue-wait-ms"]) >= 0
    assert r.headers["x-place-decision"] == "worker_a"
    assert post.await_count == 1
    assert gateway_main.queues.inflight("worker_a") == 0
    assert reg.snapshots["worker_a"].inflight == 0


def test_slot_released_on_upstream_error(gw) -> None:
    client, _ = gw
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = httpx.ConnectError("refused")
        assert serve(client).status_code == 503
    assert gateway_main.queues.inflight("worker_a") == 0


def test_slot_released_on_stream_end(gw) -> None:
    client, _ = gw

    class Upstream:
        status_code = 200

        async def aiter_lines(self):
            yield "data: hi"

    class Ctx:
        async def __aenter__(self):
            return Upstream()

        async def __aexit__(self, *a):
            return False

    with patch("httpx.AsyncClient.stream", return_value=Ctx()):
        r = client.post("/serve", json={"messages": MSG, "stream": True})
    assert r.status_code == 200 and "data: hi" in r.text
    assert gateway_main.queues.inflight("worker_a") == 0


def test_timeout_queue_never_dispatched(gw) -> None:
    client, _ = gw
    q = gateway_main.queues
    loop = asyncio.new_event_loop()
    held = loop.run_until_complete(q.acquire("worker_a", "interactive", 1))  # occupy the slot
    try:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            r = serve(client, **{"x-tenant-id": "acme"})
        assert r.status_code == 503 and r.json()["error"] == "timeout_queue"
        assert r.headers["x-queue-decision"] == "timeout_queue"
        assert "retry-after" in r.headers
        post.assert_not_awaited()
        assert q.depth("worker_a") == 0
    finally:
        held.release()
        loop.close()
    assert 'queue_error_total{class="interactive",reason="timeout_queue"}' in (
        metrics.render()[0].decode()
    )


def test_deadline_shorter_than_queue_timeout(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setattr(gateway_main.queues, "cfg", cfg(timeout_s=30.0))
    loop = asyncio.new_event_loop()
    held = loop.run_until_complete(gateway_main.queues.acquire("worker_a", "batch", 1))
    try:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            start = time.monotonic()
            r = serve(client, **{"x-deadline-ms": "100"})
        assert r.json()["error"] == "timeout_queue" and time.monotonic() - start < 5
        post.assert_not_awaited()
    finally:
        held.release()
        loop.close()


def test_queue_full_503_and_tenant_refund(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setattr(gateway_main, "quota", TenantQuota(frozenset({"acme"}), max_concurrency=1))
    monkeypatch.setattr(gateway_main.queues, "cfg", cfg(max_depth=0))
    loop = asyncio.new_event_loop()
    held = loop.run_until_complete(gateway_main.queues.acquire("worker_a", "interactive", 1))
    try:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            r = serve(client, **{"x-tenant-id": "acme"})
        assert r.status_code == 503 and r.json()["error"] == "queue_full"
        assert r.headers["x-queue-decision"] == "queue_full"
        post.assert_not_awaited()
        # tenant lease was refunded/released: the single concurrency slot is free again
        assert not isinstance(gateway_main.quota.acquire("acme", 1, time.monotonic()), Shed)
    finally:
        held.release()
        loop.close()

"""slice_oom: a worker's GPU out-of-memory error stays local, is counted, and makes it requalify."""

import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway import metrics
from infra.inference.gateway.overflow import OverflowConfig
from infra.inference.gateway.upstream import classify_error
from infra.inference.gateway.workers import Registry, Worker

MSG = [{"role": "user", "content": "hello"}]
OVERFLOW_URL = "https://overflow.example.test/v1/chat/completions"
OOM_BODY = "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB"


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (500, OOM_BODY, "slice_oom"),
        (503, "CUDA error: out of memory", "slice_oom"),
        (500, "cudaErrorMemoryAllocation", "slice_oom"),
        (500, "Failed to allocate 1GiB of device memory", "slice_oom"),
        (500, "OUT OF MEMORY", "slice_oom"),
        (500, "internal error", None),
        (503, "server busy", None),
        (400, OOM_BODY, None),  # a client error is never an OOM of the slice
        (200, OOM_BODY, None),
        (500, "", None),
    ],
)
def test_classify_error(status, body, expected) -> None:
    assert classify_error(status, body) == expected


@pytest.fixture
def gw(monkeypatch):
    reg = Registry([Worker("worker_a", "http://worker-a:8000")], require_warm=True)
    snap = reg.snapshots["worker_a"]
    snap.healthy, snap.warm, snap.observed_at = True, True, time.monotonic()
    monkeypatch.setattr(gateway_main, "registry", reg)
    monkeypatch.setattr(
        gateway_main,
        "OVERFLOW_CFG",
        OverflowConfig(True, "acme-cloud", "big-model", OVERFLOW_URL, "sk-test"),
    )
    return TestClient(gateway_main.app), reg


def _post(local_response):
    calls = []

    async def post(self, url, **kw):
        calls.append(url)
        if url == OVERFLOW_URL:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "hi"}}]}
            )
        return local_response

    return post, calls


def _count(worker: str = "worker_a") -> float:
    for family in metrics.REGISTRY.collect():
        if family.name == "slice_oom":
            return sum(
                s.value
                for s in family.samples
                if s.labels.get("worker") == worker and s.name.endswith("_total")
            )
    return 0.0


@pytest.mark.parametrize("status", [500, 503])
def test_oom_stays_local_even_when_overflow_is_configured(gw, status) -> None:
    client, reg = gw
    before = _count()
    post, calls = _post(httpx.Response(status, text=OOM_BODY))
    with patch.object(httpx.AsyncClient, "post", post):
        r = client.post("/serve", json={"messages": MSG, "model": "local"})
    assert r.status_code == status
    assert r.headers["x-upstream-reason"] == "slice_oom"
    assert "x-overflow" not in r.headers and OVERFLOW_URL not in calls
    assert _count() == before + 1
    snap = reg.snapshots["worker_a"]
    assert not snap.warm and snap.healthy_streak == 0  # must requalify


def test_a_plain_503_still_overflows(gw) -> None:
    client, _ = gw
    post, calls = _post(httpx.Response(503, text="server busy"))
    with patch.object(httpx.AsyncClient, "post", post):
        r = client.post("/serve", json={"messages": MSG, "model": "local"})
    assert r.status_code == 200 and OVERFLOW_URL in calls
    assert "x-upstream-reason" not in r.headers


def test_a_plain_500_is_not_counted_as_oom(gw) -> None:
    client, reg = gw
    before = _count()
    post, _ = _post(httpx.Response(500, text="internal error"))
    with patch.object(httpx.AsyncClient, "post", post):
        r = client.post("/serve", json={"messages": MSG, "model": "local"})
    assert r.status_code == 500 and "x-upstream-reason" not in r.headers
    assert _count() == before and reg.snapshots["worker_a"].warm


def test_mark_cold_invalidates_a_probe_in_flight() -> None:
    reg = Registry([Worker("worker_a", "http://a:8000")], require_warm=True)
    snap = reg.snapshots["worker_a"]
    snap.warm, snap.healthy_streak, epoch = True, 5, snap.health_epoch
    reg.mark_cold("worker_a", "slice_oom")
    assert not snap.warm and snap.healthy_streak == 0 and snap.health_epoch == epoch + 1


def test_mark_cold_is_harmless_when_the_warm_gate_is_off() -> None:
    reg = Registry([Worker("worker_a", "http://a:8000")])
    snap = reg.snapshots["worker_a"]
    snap.warm = True
    reg.mark_cold("worker_a", "slice_oom")
    assert snap.warm  # legacy mode: warm only means "scraped once"

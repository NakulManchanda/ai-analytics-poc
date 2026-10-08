"""Warm gate: a worker takes traffic only after N healthy scrapes AND one probe request."""

import asyncio
import functools
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway.admission import AdmitConfig
from infra.inference.gateway.workers import Registry, Worker, build_registry

PROM = (Path(__file__).parent / "fixtures" / "vllm-worker-b.prom").read_text()
MSG = [{"role": "user", "content": "hello"}]


class FakeClient:
    """Stands in for httpx.AsyncClient: scrapes and probes are scripted per test."""

    def __init__(self, *, scrape_ok=True, probe_ok=True) -> None:
        self.scrape_ok, self.probe_ok = scrape_ok, probe_ok
        self.posts: list[dict] = []

    async def get(self, url, **_):
        if not self.scrape_ok:
            raise httpx.ConnectError("down")
        return httpx.Response(200, text=PROM, request=httpx.Request("GET", url))

    async def post(self, url, json=None, **_):
        self.posts.append({"url": url, "json": json})
        if not self.probe_ok:
            raise httpx.ConnectError("probe failed")
        return httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", url))


def sync(fn):
    """The suite has no async plugin: run each async test on its own event loop."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def reg(**kw) -> Registry:
    return Registry([Worker("worker_a", "http://a:8000")], require_warm=True, **kw)


async def settle(registry: Registry) -> None:
    await asyncio.gather(*registry._probes.values())


@sync
async def test_not_warm_before_min_scrapes_and_probe() -> None:
    r, client = reg(warm_min_scrapes=3), FakeClient()
    snap = r.snapshots["worker_a"]
    for _ in range(2):
        await r.refresh(client)
    assert snap.healthy and not snap.warm and client.posts == []
    assert r.serving_snapshots() == []


@sync
async def test_warm_after_min_scrapes_and_successful_probe() -> None:
    r, client = reg(warm_min_scrapes=3, probe_model="m"), FakeClient()
    for _ in range(3):
        await r.refresh(client)
    await settle(r)
    snap = r.snapshots["worker_a"]
    assert snap.warm and snap.warmed_at is not None
    assert r.serving_snapshots() == [snap]
    probe = client.posts[0]
    assert probe["url"] == "http://a:8000/v1/chat/completions"
    assert probe["json"]["model"] == "m" and probe["json"]["max_tokens"] == 1


@sync
async def test_probe_failure_leaves_worker_cold_and_is_retried() -> None:
    r, client = reg(warm_min_scrapes=1), FakeClient(probe_ok=False)
    seen = []
    r.on_probe = lambda wid, result: seen.append(result)
    await r.refresh(client)
    await settle(r)
    assert not r.snapshots["worker_a"].warm and seen == ["error"]
    client.probe_ok = True
    await r.refresh(client)
    await settle(r)
    assert r.snapshots["worker_a"].warm and seen == ["error", "ok"]


@sync
async def test_failed_scrape_makes_a_warm_worker_cold_again() -> None:
    r, client = reg(warm_min_scrapes=1), FakeClient()
    await r.refresh(client)
    await settle(r)
    assert r.snapshots["worker_a"].warm
    client.scrape_ok = False
    await r.refresh(client)
    snap = r.snapshots["worker_a"]
    assert not snap.warm and not snap.healthy and snap.healthy_streak == 0
    assert r.serving_snapshots() == []


@sync
async def test_returning_worker_must_pass_the_gate_again() -> None:
    r, client = reg(warm_min_scrapes=2), FakeClient()
    for _ in range(2):
        await r.refresh(client)
    await settle(r)
    client.scrape_ok = False
    await r.refresh(client)
    client.scrape_ok = True
    await r.refresh(client)  # one healthy scrape after the outage is not enough
    assert not r.snapshots["worker_a"].warm and len(client.posts) == 1
    await r.refresh(client)
    await settle(r)
    assert r.snapshots["worker_a"].warm and len(client.posts) == 2


@sync
async def test_only_one_probe_in_flight_per_worker() -> None:
    r, client = reg(warm_min_scrapes=1), FakeClient()
    gate = asyncio.Event()
    orig = client.post

    async def slow(url, json=None, **kw):
        await gate.wait()
        return await orig(url, json=json, **kw)

    client.post = slow
    await r.refresh(client)
    await r.refresh(client)
    gate.set()
    await settle(r)
    assert len(client.posts) == 1


@sync
async def test_gate_off_keeps_legacy_warm_after_one_scrape() -> None:
    r = Registry([Worker("worker_a", "http://a:8000")])  # require_warm defaults to False
    client = FakeClient()
    await r.refresh(client)
    assert r.snapshots["worker_a"].warm and client.posts == []
    assert len(r.serving_snapshots()) == 1


def test_build_registry_reads_warm_gate_env(monkeypatch) -> None:
    monkeypatch.setenv("WARM_GATE", "1")
    monkeypatch.setenv("WARM_MIN_SCRAPES", "5")
    monkeypatch.setenv("WARM_PROBE_MODEL", "x/y")
    r = build_registry()
    assert (r.require_warm, r.warm_min_scrapes, r.probe_model) == (True, 5, "x/y")
    monkeypatch.delenv("WARM_GATE")
    assert build_registry().require_warm is False


@pytest.fixture
def cold_gw(monkeypatch):
    r = Registry([Worker("worker_a", "http://a:8000")], require_warm=True)
    s = r.snapshots["worker_a"]
    s.healthy, s.observed_at = True, time.monotonic()  # scraped fine, but not warm
    monkeypatch.setattr(gateway_main, "registry", r)
    monkeypatch.setattr(gateway_main, "ADMIT_CFG", AdmitConfig())
    return TestClient(gateway_main.app), s


def test_cold_worker_gets_no_traffic_through_the_gateway(cold_gw) -> None:
    client, _ = cold_gw
    resp = client.post("/serve", json={"messages": MSG})
    assert resp.status_code == 503
    assert resp.headers["x-admit-decision"] == "shed:no_signal"


def test_worker_receives_traffic_once_warm(cold_gw) -> None:
    client, snap = cold_gw
    snap.warm = True

    async def ok(self, url, **_):
        return httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", url))

    with patch("httpx.AsyncClient.post", ok):
        resp = client.post("/serve", json={"messages": MSG})
    assert resp.status_code == 200

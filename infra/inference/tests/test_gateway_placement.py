"""Tests: guard, snapshots, placement policies, gateway metrics (#122 slice 1)."""

import asyncio
import random
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway.guard import inspect
from infra.inference.gateway.placement import (
    PlacementDecision,
    PlacementError,
    PlacementRequest,
    pick,
)
from infra.inference.gateway.workers import Registry, Worker, WorkerSnapshot, parse_vllm_metrics

FIXTURE = Path(__file__).parent / "fixtures" / "vllm-worker-b.prom"
MSG = [{"role": "user", "content": "hi"}]


def snap(wid, *, running=0, waiting=0, kv_free=1.0, age=0.0, healthy=True, prefixes=None):
    now = time.monotonic()
    s = WorkerSnapshot(
        Worker(wid, f"http://{wid}:8000"), now - age, running, waiting, kv_free, healthy, healthy
    )
    s.prefixes = prefixes or {}
    return s


def two(**kw):
    return [snap("worker_a", **kw.get("a", {})), snap("worker_b", **kw.get("b", {}))]


# --- guard ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,code",
    [
        ([], "malformed_payload"),
        ("x", "malformed_payload"),
        ({}, "missing_messages"),
        ({"messages": []}, "missing_messages"),
        ({"messages": ["nope"]}, "malformed_payload"),
        ({"messages": [{"role": "user", "content": "x" * 4000}]}, "prompt_too_long"),
    ],
)
def test_guard_rejects(payload, code) -> None:
    g = inspect(payload, max_tokens=100)
    assert not g.ok and g.code == code and g.reason


def test_guard_allows_and_header_overrides_estimate() -> None:
    assert inspect({"messages": MSG}, max_tokens=100).ok
    assert not inspect({"messages": MSG}, estimated_tokens_header="101", max_tokens=100).ok
    assert inspect({"messages": MSG}, estimated_tokens_header="bogus", max_tokens=100).ok


def test_guard_limit_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_PROMPT_TOKENS", "5")
    assert inspect({"messages": MSG}, estimated_tokens_header="6").code == "prompt_too_long"


# --- snapshots -----------------------------------------------------------------------------


def test_parse_vllm_fixture_and_staleness() -> None:
    vals = parse_vllm_metrics(FIXTURE.read_text())
    assert vals == {"running": 0.0, "waiting": 0.0, "kv_used": 0.0}
    s = snap("worker_a", age=10)
    assert s.is_stale(5) and not snap("worker_a", age=1).is_stale(5)
    assert WorkerSnapshot(Worker("x", "u")).is_stale(5)  # never observed


def test_registry_refresh_success_and_failure() -> None:
    text = (
        FIXTURE.read_text()
        .replace(
            'vllm:num_requests_waiting{engine="0",model_name="Qwen/Qwen3-0.6B"} 0.0',
            'vllm:num_requests_waiting{engine="0",model_name="Qwen/Qwen3-0.6B"} 3.0',
        )
        .replace(
            'vllm:kv_cache_usage_perc{engine="0",model_name="Qwen/Qwen3-0.6B"} 0.0',
            'vllm:kv_cache_usage_perc{engine="0",model_name="Qwen/Qwen3-0.6B"} 0.25',
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=text) if request.url.host == "a" else httpx.Response(500)

    reg = Registry([Worker("worker_a", "http://a:8000"), Worker("worker_b", "http://b:8000")])

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await reg.refresh(client)

    asyncio.run(run())
    a, b = reg.snapshots["worker_a"], reg.snapshots["worker_b"]
    assert (a.healthy, a.warm, a.waiting, a.kv_free_ratio) == (True, True, 3, 0.75)
    assert a.age() < 1
    assert not b.healthy and b.observed_at is None


# --- policies ------------------------------------------------------------------------------


def test_round_robin_alternates() -> None:
    ws = two()
    picks = [
        pick(PlacementRequest(), ws, policy="round_robin", rr_index=i).chosen_worker
        for i in range(4)
    ]
    assert picks == ["worker_a", "worker_b"] * 2


def test_least_loaded_and_queue_depth_participates() -> None:
    req = PlacementRequest()
    # a has fewer running requests but a deeper waiting queue -> b wins
    ws = two(a={"running": 1, "waiting": 3}, b={"running": 4, "waiting": 0})
    assert pick(req, ws, policy="least_loaded").chosen_worker == "worker_b"
    ws = two(a={"running": 1, "waiting": 0}, b={"running": 4, "waiting": 0})
    assert pick(req, ws, policy="least_loaded").chosen_worker == "worker_a"


def test_p2c_picks_less_loaded_of_pair() -> None:
    ws = two(a={"running": 9}, b={"running": 1})
    d = pick(PlacementRequest(), ws, policy="p2c", rng=random.Random(0))
    assert d.chosen_worker == "worker_b" and d.placement_reason == "p2c"


def _with_prefix(ws, wid, prefix="p1", tokens=1000):
    from infra.inference.gateway.workers import PrefixBelief

    next(w for w in ws if w.id == wid).prefixes[prefix] = PrefixBelief(time.monotonic(), tokens)


def test_prefix_then_load_sticks_then_spills() -> None:
    req = PlacementRequest("p1", est_tokens=1100)
    ws = two(a={"running": 5})  # a is busier but owns the prefix
    _with_prefix(ws, "worker_a")
    d = pick(req, ws, policy="prefix_then_load")
    assert (d.chosen_worker, d.placement_reason, d.intended_action) == (
        "worker_a",
        "prefix_affinity",
        "local_reuse",
    )
    assert d.estimated_reusable_tokens == 1000 and d.prior_worker == "worker_a"

    # KV >= 70% used on the owner -> spill to b, cold destination => recompute
    ws = two(a={"kv_free": 0.25})
    _with_prefix(ws, "worker_a")
    d = pick(req, ws, policy="prefix_then_load")
    assert (d.chosen_worker, d.placement_reason, d.intended_action) == (
        "worker_b",
        "prefix_owner_kv_pressure",
        "recompute",
    )
    assert d.prior_worker == "worker_a" and d.estimated_reusable_tokens == 0


def test_prefix_locality_is_by_prefix_identity_not_tenant() -> None:
    ws = two()
    _with_prefix(ws, "worker_b", prefix="p-other")
    d = pick(PlacementRequest("p1", 100), ws, policy="prefix_then_load", rng=random.Random(1))
    assert d.prior_worker is None and d.placement_reason == "no_prefix_known"


def test_destination_hit_when_other_worker_holds_prefix_too() -> None:
    ws = two(b={"running": 5})
    _with_prefix(ws, "worker_a")
    time.sleep(0.001)
    _with_prefix(ws, "worker_b")  # b is the most recent holder -> prior=b
    d = pick(PlacementRequest("p1", 1000), ws, policy="least_loaded")
    assert (d.chosen_worker, d.prior_worker, d.intended_action) == (
        "worker_a",
        "worker_b",
        "destination_hit",
    )


def test_same_trace_under_three_policies() -> None:
    trace = [PlacementRequest("p1", 1000)] * 6

    def replay(policy):
        ws = two(a={"running": 2}, b={"running": 0})
        _with_prefix(ws, "worker_a")
        out = []
        for i, r in enumerate(trace):
            d = pick(r, ws, policy=policy, rr_index=i, rng=random.Random(i))
            out.append(d.chosen_worker)
        return out

    assert replay("round_robin") == ["worker_a", "worker_b"] * 3
    assert replay("least_loaded") == ["worker_b"] * 6
    assert replay("prefix_then_load") == ["worker_a"] * 6


def test_hop_is_never_produced() -> None:
    for policy in ("round_robin", "least_loaded", "p2c", "prefix_then_load"):
        ws = two(a={"kv_free": 0.25})
        _with_prefix(ws, "worker_a")
        for i in range(4):
            d = pick(
                PlacementRequest("p1", 1000), ws, policy=policy, rr_index=i, rng=random.Random(i)
            )
            assert isinstance(d, PlacementDecision) and d.intended_action != "hop"


# --- stale / unhealthy / saturated / forced ------------------------------------------------


def test_stale_workers_dropped_while_fresh_remains() -> None:
    ws = [snap("worker_a", age=60), snap("worker_b", running=50)]
    d = pick(PlacementRequest(), ws, policy="least_loaded", stale_after=5)
    assert d.chosen_worker == "worker_b" and d.fallback is None


def test_all_stale_conservative_fallback_is_flagged() -> None:
    ws = [snap("worker_a", age=60, running=3), snap("worker_b", age=60, running=1)]
    d = pick(PlacementRequest(), ws, policy="least_loaded", stale_after=5)
    assert d.chosen_worker == "worker_b" and d.fallback == "stale_snapshot" and d.snapshot_age >= 60


def test_no_healthy_worker_fails_closed() -> None:
    ws = [snap("worker_a", healthy=False), snap("worker_b", healthy=False)]
    assert pick(PlacementRequest(), ws, policy="least_loaded") == PlacementError(
        "no_healthy_worker"
    )


def test_saturated_worker_avoided_when_alternative_exists() -> None:
    ws = two(a={"kv_free": 0.05}, b={"running": 20})
    assert pick(PlacementRequest(), ws, policy="least_loaded").chosen_worker == "worker_b"


def test_unknown_policy_is_error() -> None:
    assert pick(PlacementRequest(), two(), policy="nope") == PlacementError("unknown_policy")


def test_forced_worker_only_when_allowed() -> None:
    req = PlacementRequest(forced_worker="worker_b")
    ws = two(b={"running": 99})
    ignored = pick(req, ws, policy="least_loaded", allow_forced=False)
    assert ignored.chosen_worker == "worker_a"
    forced = pick(req, ws, policy="least_loaded", allow_forced=True)
    assert (forced.chosen_worker, forced.placement_policy) == ("worker_b", "forced")
    bad = pick(PlacementRequest(forced_worker="zzz"), ws, policy="least_loaded", allow_forced=True)
    assert bad == PlacementError("unknown_forced_worker")


# --- gateway end to end --------------------------------------------------------------------


@pytest.fixture
def gw(monkeypatch):
    reg = Registry(
        [Worker("worker_a", "http://worker-a:8000"), Worker("worker_b", "http://worker-b:8000")]
    )
    for s in reg.snapshots.values():
        s.healthy, s.observed_at = True, time.monotonic()
    monkeypatch.setattr(gateway_main, "registry", reg)
    monkeypatch.setattr(gateway_main, "PLACEMENT_POLICY", "least_loaded")
    return TestClient(gateway_main.app), reg


def _ok(url, **_):
    return httpx.Response(200, json={"choices": [], "url": url})


def test_gateway_picks_either_worker_and_reports_it(gw) -> None:
    client, reg = gw
    body = {"messages": MSG}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        r1 = client.post("/serve", json=body)
        reg.snapshots["worker_a"].running = 10  # scrape says A is busy now
        r2 = client.post("/serve", json=body)
    assert r1.headers["x-place-decision"] == "worker_a"
    assert r2.headers["x-place-decision"] == "worker_b"
    assert post.call_args_list[0].args[0].startswith("http://worker-a:8000")
    assert post.call_args_list[1].args[0].startswith("http://worker-b:8000")
    assert r2.headers["x-placement-policy"] == "least_loaded"
    assert r2.headers["x-intended-action"] == "recompute"
    assert all(s.inflight == 0 for s in reg.snapshots.values())


def test_gateway_forced_header_gated_by_env(gw, monkeypatch) -> None:
    client, _ = gw
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        r = client.post("/serve", json={"messages": MSG}, headers={"x-force-worker": "worker_b"})
        assert r.headers["x-place-decision"] == "worker_a"
        monkeypatch.setenv("ALLOW_FORCED_PLACEMENT", "1")
        r = client.post("/serve", json={"messages": MSG}, headers={"x-force-worker": "worker_b"})
        assert r.headers["x-place-decision"] == "worker_b"


def test_gateway_guard_reject_and_placement_error(gw) -> None:
    client, reg = gw
    r = client.post("/serve", json={"messages": []})
    assert r.status_code == 400 and r.headers["x-guard-decision"] == "reject:missing_messages"
    r = client.post("/serve", content=b"not json")
    assert r.status_code == 400 and r.headers["x-guard-decision"] == "reject:malformed_payload"
    r = client.post(
        "/serve", json={"messages": MSG}, headers={"x-estimated-prompt-tokens": "10000000"}
    )
    assert r.status_code == 413
    for s in reg.snapshots.values():
        s.healthy = False
    r = client.post("/serve", json={"messages": MSG})
    assert r.status_code == 503 and r.json()["error"] == "no_healthy_worker"
    assert r.headers["x-place-decision"] == "none"


def test_metrics_endpoint_nonempty_and_bounded(gw) -> None:
    client, _ = gw
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        client.post(
            "/serve",
            json={"messages": MSG},
            headers={"x-request-id": "high-card-1", "x-prefix-id": "pfx-9"},
        )
    client.post("/serve", json={"messages": []})
    text = client.get("/metrics").text
    assert text.strip()
    for name in (
        "gateway_requests_total",
        "guard_reject_total",
        "orch_pick_total",
        "placement_error_total",
        "worker_health",
        "worker_snapshot_age_seconds",
    ):
        assert name in text
    assert 'orch_pick_total{policy="least_loaded",reason="least_loaded",worker="worker_a"}' in text
    assert 'worker_health{state="healthy",worker="worker_b"} 1.0' in text
    assert "high-card-1" not in text and "pfx-9" not in text


def test_gateway_imports_flat_like_the_configmap_deployment() -> None:
    """The k8s Deployment mounts the gateway files flat and runs `uvicorn main:app`."""
    import subprocess
    import sys

    gateway_dir = Path(gateway_main.__file__).parent
    code = "import main; assert main.app.title"
    subprocess.run([sys.executable, "-c", code], cwd=gateway_dir, check=True)

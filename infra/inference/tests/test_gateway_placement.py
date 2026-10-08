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
    reg.snapshots["worker_b"].running = 1  # not tied, so the first pick is deterministic
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
    assert all(
        s.inflight == 0 and s.pending == 0 and s.unobserved == 0 for s in reg.snapshots.values()
    )


def test_gateway_forced_header_gated_by_env(gw, monkeypatch) -> None:
    client, reg = gw
    reg.snapshots["worker_b"].running = 1  # not tied, so least_loaded picks worker_a
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
    assert r.status_code == 503 and r.json()["error"] == "no_signal"  # admission fails closed first
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


def test_worker_warm_gauge_healthy_but_not_warm() -> None:
    from infra.inference.gateway import metrics

    reg = Registry([Worker("warm_w", "http://a:1"), Worker("cold_w", "http://b:1")])
    now = time.monotonic()
    for wid, warm in (("warm_w", True), ("cold_w", False)):
        s = reg.snapshots[wid]
        s.healthy, s.warm, s.observed_at = True, warm, now
    metrics.observe_snapshots(reg.snapshots.values(), 5.0)
    get = metrics.REGISTRY.get_sample_value
    assert get("worker_warm", {"worker": "warm_w"}) == 1
    assert get("worker_warm", {"worker": "cold_w"}) == 0
    assert get("worker_health", {"worker": "cold_w", "state": "healthy"}) == 1


def _belief_after(client, reg, headers, content="x" * 400):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        r = client.post(
            "/serve",
            json={"messages": [{"role": "user", "content": content}]},
            headers={"x-prefix-id": "sys1", **headers},
        )
    return r, max(
        (s.prefixes["sys1"] for s in reg.snapshots.values() if "sys1" in s.prefixes),
        key=lambda b: b.observed_at,
    )


def test_prefix_tokens_header_bounds_reusable_belief(gw) -> None:
    client, reg = gw
    # two conversations, same system-only id, very different totals: belief stays the system size
    _, b1 = _belief_after(client, reg, {"x-prefix-tokens": "50"}, "a" * 4000)
    assert b1.tokens == 50
    _, b2 = _belief_after(client, reg, {"x-prefix-tokens": "50"}, "b" * 12000)
    assert b2.tokens == 50  # not the ~3000-token total


def test_prefix_tokens_clamped_and_legacy_path(gw) -> None:
    client, reg = gw
    _, b = _belief_after(client, reg, {"x-prefix-tokens": "999999"}, "a" * 400)
    assert 0 < b.tokens <= 110  # clamped to the ~100-token estimate
    _, b = _belief_after(client, reg, {"x-prefix-tokens": "-5"})
    assert b.tokens > 50  # negative -> invalid -> legacy
    _, b = _belief_after(client, reg, {"x-prefix-tokens": "junk"})
    assert b.tokens > 50  # invalid -> legacy: whole prompt estimate
    _, b = _belief_after(client, reg, {})
    assert b.tokens > 50  # absent -> legacy (over-counts prompts with a per-turn suffix)


@pytest.mark.parametrize(
    "belief,est",
    [
        (900, 1000),
        (800, 1000),
        (799, 1000),
        (100, 1000),  # small system prefix vs a prompt that has grown large
        (100, 8000),
        (5000, 1000),
    ],
)
def test_conversation_stays_on_its_owner_however_the_prompt_grows(belief, est) -> None:
    ws = two()
    _with_prefix(ws, "worker_a", tokens=belief)
    for seed in range(5):
        rng = random.Random(seed)
        d = pick(PlacementRequest("p1", est), ws, policy="prefix_then_load", rng=rng)
        assert (d.chosen_worker, d.placement_reason) == ("worker_a", "prefix_affinity")


def test_owner_queue_saturation_moves_only_to_a_less_loaded_worker() -> None:
    req = PlacementRequest("p1", 1000)
    ws = two(a={"waiting": 4}, b={})  # owner has 4 waiting (>= spill_queue), b is idle
    _with_prefix(ws, "worker_a")
    d = pick(req, ws, policy="prefix_then_load", spill_queue=4)
    assert (d.chosen_worker, d.placement_reason) == ("worker_b", "prefix_owner_saturated")

    ws = two(a={"waiting": 4}, b={"waiting": 4})  # b is just as loaded: moving would lose the KV
    _with_prefix(ws, "worker_a")
    d = pick(req, ws, policy="prefix_then_load", spill_queue=4)
    assert (d.chosen_worker, d.placement_reason) == ("worker_a", "prefix_affinity")


def test_below_the_spill_threshold_the_owner_keeps_the_conversation() -> None:
    ws = two(a={"waiting": 3}, b={})
    _with_prefix(ws, "worker_a")
    d = pick(PlacementRequest("p1", 1000), ws, policy="prefix_then_load", spill_queue=4)
    assert (d.chosen_worker, d.placement_reason) == ("worker_a", "prefix_affinity")


def test_pending_and_queued_requests_count_toward_the_owner_saturation() -> None:
    ws = two(b={})
    _with_prefix(ws, "worker_a")
    ws[0].queued, ws[0].pending = 2, 2
    d = pick(PlacementRequest("p1", 1000), ws, policy="prefix_then_load", spill_queue=4)
    assert d.chosen_worker == "worker_b" and d.placement_reason == "prefix_owner_saturated"


def test_equal_load_ties_are_broken_randomly_not_always_toward_the_first_worker() -> None:
    ws = two()
    chosen = {
        pick(PlacementRequest(), ws, policy="least_loaded", rng=random.Random(i)).chosen_worker
        for i in range(20)
    }
    assert chosen == {"worker_a", "worker_b"}


def test_pending_placements_spread_a_burst_across_workers() -> None:
    ws = two()
    picks = []
    for i in range(8):
        d = pick(PlacementRequest(), ws, policy="least_loaded", rng=random.Random(i))
        next(w for w in ws if w.id == d.chosen_worker).pending += 1  # what the gateway does
        picks.append(d.chosen_worker)
    assert picks.count("worker_a") == picks.count("worker_b") == 4


# --- batch slot cap is enforced on the worker actually chosen -------------------------------


def test_batch_is_not_placed_on_a_worker_at_its_slot_cap_even_when_it_owns_the_conversation() -> (
    None
):
    ws = two(a={"running": 6}, b={})
    _with_prefix(ws, "worker_a")
    batch = PlacementRequest("p1", 1000, workload_class="batch")
    d = pick(
        batch, ws, policy="prefix_then_load", batch_slot_limit=6, rng=random.Random(0)
    )
    assert d.chosen_worker == "worker_b"
    interactive = pick(
        PlacementRequest("p1", 1000), ws, policy="prefix_then_load", batch_slot_limit=6
    )
    assert (interactive.chosen_worker, interactive.placement_reason) == (
        "worker_a",
        "prefix_affinity",
    )


def test_batch_with_every_worker_at_its_cap_is_a_placement_error() -> None:
    ws = two(a={"running": 6}, b={"running": 7})
    d = pick(
        PlacementRequest(workload_class="batch"),
        ws,
        policy="least_loaded",
        batch_slot_limit=6,
    )
    assert isinstance(d, PlacementError) and d.reason == "batch_slot_cap"


def test_batch_cap_counts_dispatches_the_scrape_has_not_seen() -> None:
    # Review case: the scrape shows 5 running and the gateway dispatched 3 more since. Real use may
    # be 8, so a batch cap of 6 must not allow another batch placement there.
    ws = two(a={"running": 5}, b={"running": 6})
    ws[0].unobserved = 3
    batch = PlacementRequest(workload_class="batch")
    d = pick(batch, ws, policy="least_loaded", batch_slot_limit=6)
    assert isinstance(d, PlacementError) and d.reason == "batch_slot_cap"


def test_batch_cap_does_not_double_count_dispatches_the_scrape_already_shows() -> None:
    ws = two(a={"running": 5}, b={"running": 6})
    ws[0].inflight = 5  # all five are in the scrape's running count already
    batch = PlacementRequest(workload_class="batch")
    d = pick(batch, ws, policy="least_loaded", batch_slot_limit=6)
    assert isinstance(d, PlacementDecision) and d.chosen_worker == "worker_a"


def test_occupancy_follows_dispatch_finish_and_scrape_generations() -> None:
    s = snap("worker_a", running=5)
    first = s.note_dispatch()
    s.note_dispatch()
    assert s.occupied == 7
    s.note_finish(first)  # dispatched after the latest scrape and now done
    assert s.occupied == 6
    old = s.note_dispatch()
    s.running = 8  # a new scrape arrives and now includes everything dispatched so far
    s.note_scrape()
    assert s.occupied == 8 and s.unobserved == 0
    s.note_finish(old)  # finished work that the new scrape already absorbed is not subtracted again
    assert s.occupied == 8
    after = s.note_dispatch()
    s.note_finish(after)
    assert s.occupied == 8


def test_no_batch_cap_when_no_limit_is_given() -> None:
    ws = two(a={"running": 7}, b={"running": 7})
    d = pick(PlacementRequest(workload_class="batch"), ws, policy="least_loaded")
    assert isinstance(d, PlacementDecision)


def test_gateway_never_routes_admitted_batch_to_the_owner_at_the_batch_cap(
    gw, monkeypatch
) -> None:
    from infra.inference.gateway.admission import AdmitConfig

    client, reg = gw
    monkeypatch.setattr(gateway_main, "PLACEMENT_POLICY", "prefix_then_load")
    monkeypatch.setattr(
        gateway_main, "ADMIT_CFG", AdmitConfig(max_decode_slots=8)
    )  # batch cap 6
    reg.snapshots["worker_a"].running = 6
    reg.record_prefix("worker_a", "p1", 1000)
    body = {"messages": MSG}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        # Interactive first: placing the batch request on worker_b moves the prefix belief there.
        interactive = client.post("/serve", json=body, headers={"x-prefix-id": "p1"})
        batch = client.post(
            "/serve",
            json=body,
            headers={"x-prefix-id": "p1", "x-request-priority": "batch"},
        )
    assert interactive.headers["x-place-decision"] == "worker_a"
    assert batch.status_code == 200 and batch.headers["x-place-decision"] == "worker_b"

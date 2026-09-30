"""Admission, tenant quota and pipeline tests (#122 slice 2)."""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway.admission import (
    Admit,
    AdmitConfig,
    AdmitRequest,
    Shed,
    should_shed,
)
from infra.inference.gateway.tenants import TenantQuota
from infra.inference.gateway.workers import Registry, Worker, WorkerSnapshot

MSG = [{"role": "user", "content": "hello"}]
NOW = 1000.0
CFG = AdmitConfig(
    max_decode_slots=4,
    kv_free_min=0.10,
    prefill_tokens_per_s=1000.0,
    queue_wait_per_waiting_s=0.5,
    stale_after_s=5.0,
)


def snap(wid="w", *, running=0, waiting=0, kv=0.9, healthy=True, age=0.0) -> WorkerSnapshot:
    s = WorkerSnapshot(Worker(wid, f"http://{wid}:8000"))
    s.running, s.waiting, s.kv_free_ratio, s.healthy = running, waiting, kv, healthy
    s.observed_at = None if age is None else NOW - age
    return s


def decide(*snaps, **req) -> Admit | Shed:
    return should_shed(AdmitRequest(**req), list(snaps), now=NOW, cfg=CFG)


# --- pure admission ------------------------------------------------------------------


def test_admit_when_healthy_and_no_deadline() -> None:
    r = decide(snap(), est_tokens=100)
    assert isinstance(r, Admit) and not r.shed
    assert r.inputs["workers"][0]["kv_free_ratio"] == 0.9


@pytest.mark.parametrize(
    "snaps",
    [
        [],
        [snap(healthy=False)],
        [snap(age=5.01)],  # stale
        [snap(age=None)],  # never observed
    ],
)
def test_no_signal(snaps) -> None:
    r = decide(*snaps)
    assert isinstance(r, Shed) and (r.code, r.reason) == (503, "no_signal")
    assert not r.never_overflow


def test_stale_boundary_is_fresh() -> None:
    assert isinstance(decide(snap(age=5.0)), Admit)


def test_stale_worker_ignored_when_another_is_fresh() -> None:
    assert isinstance(decide(snap("a", age=60, running=99), snap("b")), Admit)


def test_kv_pressure_boundary_uses_best_worker() -> None:
    assert isinstance(decide(snap(kv=0.10)), Admit)
    r = decide(snap("a", kv=0.09), snap("b", kv=0.05))
    assert (r.code, r.reason) == (503, "kv_pressure")
    assert isinstance(decide(snap("a", kv=0.05), snap("b", kv=0.5)), Admit)


def test_decode_slots_only_when_all_eligible_full() -> None:
    assert isinstance(decide(snap(running=3)), Admit)
    assert decide(snap(running=4)).reason == "decode_slots"
    assert isinstance(decide(snap("a", running=4), snap("b", running=3)), Admit)
    assert decide(snap("a", running=4), snap("b", running=9)).code == 503


def test_deadline_boundary_and_inputs() -> None:
    # prefill 500/1000=0.5s + waiting 2*0.5=1.0s -> 1.5s estimate
    s = snap(waiting=2)
    assert isinstance(decide(s, est_tokens=500, deadline_ms=1500), Admit)
    r = decide(s, est_tokens=500, deadline_ms=1499)
    assert (r.code, r.reason) == (504, "deadline_unachievable")
    assert r.retry_after_seconds >= 1
    assert r.inputs["workers"][0]["waiting"] == 2 and r.inputs["estimated_s"] == 1.5
    assert r.inputs["deadline_ms"] == 1499 and r.inputs["est_tokens"] == 500


def test_deadline_uses_least_queued_eligible_worker() -> None:
    r = decide(snap("a", waiting=10), snap("b", waiting=0), est_tokens=100, deadline_ms=200)
    assert isinstance(r, Admit)


def test_no_deadline_never_deadline_sheds() -> None:
    assert isinstance(decide(snap(waiting=10_000), est_tokens=10**6), Admit)


def test_reason_precedence_signal_kv_slots_deadline() -> None:
    both = snap(kv=0.01, running=9, waiting=100)
    assert decide(both, est_tokens=10**6, deadline_ms=1).reason == "kv_pressure"
    assert decide(snap(running=9, waiting=100), deadline_ms=1).reason == "decode_slots"


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_DECODE_SLOTS", "7")
    monkeypatch.setenv("KV_FREE_MIN", "0.3")
    monkeypatch.setenv("PREFILL_TOKENS_PER_S", "123")
    cfg = AdmitConfig.from_env()
    assert (cfg.max_decode_slots, cfg.kv_free_min, cfg.prefill_tokens_per_s) == (
        7,
        0.3,
        123.0,
    )


# --- tenant quota --------------------------------------------------------------------


def test_tenant_buckets_allowlist_and_other() -> None:
    q = TenantQuota(frozenset({"acme"}))
    assert q.bucket_name("acme") == "acme"
    assert q.bucket_name("stranger") == q.bucket_name(None) == "other"


def test_tenant_token_window_slides() -> None:
    q = TenantQuota(frozenset({"a"}), token_budget=100, window_s=10, max_concurrency=99)
    q.acquire("a", 60, now=0).release()
    r = q.acquire("a", 41, now=5)
    assert (r.code, r.reason, r.never_overflow) == (429, "tenant_tokens", True)
    assert r.retry_after_seconds == 5
    assert not isinstance(q.acquire("a", 40, now=5), Shed)
    assert not isinstance(q.acquire("a", 60, now=10.5), Shed)  # first entry slid out


def test_tenant_concurrency_and_idempotent_release() -> None:
    q = TenantQuota(frozenset({"a"}), max_concurrency=2)
    l1, l2 = q.acquire("a", 1, 0), q.acquire("a", 1, 0)
    r = q.acquire("a", 1, 0)
    assert (r.code, r.reason, r.never_overflow) == (429, "tenant_concurrency", True)
    l1.release()
    l1.release()  # idempotent: must not free a second slot
    assert not isinstance(l3 := q.acquire("a", 1, 0), Shed)
    assert isinstance(q.acquire("a", 1, 0), Shed)
    l2.release()
    l3.release()


def test_noisy_tenant_cannot_take_all_slots() -> None:
    q = TenantQuota(frozenset({"noisy", "quiet"}), max_concurrency=2)
    leases = [q.acquire("noisy", 1, 0) for _ in range(2)]
    assert isinstance(q.acquire("noisy", 1, 0), Shed)
    assert not isinstance(q.acquire("quiet", 1, 0), Shed)
    del leases


def test_quota_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TENANT_ALLOWLIST", "a, b,")
    monkeypatch.setenv("TENANT_MAX_CONCURRENCY", "1")
    q = TenantQuota.from_env()
    assert q.allowlist == {"a", "b"} and q.max_concurrency == 1


# --- gateway pipeline ----------------------------------------------------------------


@pytest.fixture
def gw(monkeypatch):
    reg = Registry([Worker("worker_a", "http://worker-a:8000")])
    s = reg.snapshots["worker_a"]
    s.healthy, s.observed_at = True, time.monotonic()
    monkeypatch.setattr(gateway_main, "registry", reg)
    monkeypatch.setattr(gateway_main, "ADMIT_CFG", CFG)
    monkeypatch.setattr(
        gateway_main,
        "quota",
        TenantQuota(frozenset({"acme", "beta"}), max_concurrency=1),
    )
    return TestClient(gateway_main.app), reg


def _ok(url, **_):
    return httpx.Response(200, json={"choices": []})


@pytest.mark.parametrize(
    "mutate,headers,reason,code",
    [
        (lambda s: setattr(s, "kv_free_ratio", 0.05), {}, "kv_pressure", 503),
        (lambda s: setattr(s, "running", 4), {}, "decode_slots", 503),
        (lambda s: setattr(s, "healthy", False), {}, "no_signal", 503),
        (
            lambda s: setattr(s, "waiting", 10),
            {"x-deadline-ms": "100"},
            "deadline_unachievable",
            504,
        ),
    ],
)
def test_shed_never_calls_upstream(gw, mutate, headers, reason, code, caplog) -> None:
    client, reg = gw
    mutate(reg.snapshots["worker_a"])
    caplog.set_level(logging.INFO, logger="inference.gateway")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        r = client.post(
            "/serve",
            json={"messages": MSG},
            headers={"x-tenant-id": "acme", "x-request-id": "rid-7", **headers},
        )
    post.assert_not_called()
    assert r.status_code == code and r.json()["error"] == reason
    assert r.headers["x-admit-decision"] == f"shed:{reason}"
    assert r.headers["x-guard-decision"] == "allow"
    assert r.headers["retry-after"] == str(max(1, int(r.headers["retry-after"])))
    assert reg.snapshots["worker_a"].inflight == 0
    log = [json.loads(rec.message) for rec in caplog.records if rec.message.startswith("{")]
    entry = next(e for e in log if e.get("stage") == "admit")
    assert entry["tenant_id"] == "acme" and entry["code"] == reason
    assert entry["admission_inputs"]["workers"][0]["id"] == "worker_a"
    # shed requests release their tenant slot
    assert not isinstance(gateway_main.quota.acquire("acme", 1, time.monotonic()), Shed)


def test_accept_sets_headers(gw) -> None:
    client, _ = gw
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        r = client.post("/serve", json={"messages": MSG}, headers={"x-deadline-ms": "60000"})
    assert r.status_code == 200 and r.headers["x-admit-decision"] == "accept"
    assert "retry-after" not in r.headers


def test_tenant_429_local_and_concurrency_released(gw) -> None:
    client, _ = gw
    held = gateway_main.quota.acquire("acme", 1, time.monotonic())
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        r = client.post("/serve", json={"messages": MSG}, headers={"x-tenant-id": "acme"})
        assert r.status_code == 429 and r.json()["error"] == "tenant_concurrency"
        assert r.headers["x-admit-decision"] == "shed:tenant_concurrency"
        assert "retry-after" in r.headers
        post.assert_not_called()
        # another tenant is still admitted while the noisy one is capped
        assert (
            client.post(
                "/serve", json={"messages": MSG}, headers={"x-tenant-id": "beta"}
            ).status_code
            == 200
        )
        held.release()
        assert (
            client.post(
                "/serve", json={"messages": MSG}, headers={"x-tenant-id": "acme"}
            ).status_code
            == 200
        )
        # completion released the slot again, so repeated requests keep passing
        assert (
            client.post(
                "/serve", json={"messages": MSG}, headers={"x-tenant-id": "acme"}
            ).status_code
            == 200
        )


def test_concurrency_released_on_upstream_error_and_stream_end(gw) -> None:
    client, _ = gw
    h = {"x-tenant-id": "acme"}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = httpx.ConnectError("refused")
        assert client.post("/serve", json={"messages": MSG}, headers=h).status_code == 503
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        assert client.post("/serve", json={"messages": MSG}, headers=h).status_code == 200

    class _Stream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            yield "data: hi"

    with patch("httpx.AsyncClient.stream", return_value=_Stream()):
        r = client.post("/serve", json={"messages": MSG, "stream": True}, headers=h)
    assert r.status_code == 200 and "data: hi" in r.text
    assert not isinstance(gateway_main.quota.acquire("acme", 1, time.monotonic()), Shed)


def test_guard_reject_does_not_consume_quota_and_skips_admit(gw) -> None:
    client, _ = gw
    r = client.post("/serve", json={"messages": []}, headers={"x-tenant-id": "acme"})
    assert r.status_code == 400 and r.headers["x-guard-decision"] == "reject:missing_messages"
    assert not isinstance(gateway_main.quota.acquire("acme", 1, time.monotonic()), Shed)


def test_admission_metrics_bounded(gw) -> None:
    client, reg = gw
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        client.post(
            "/serve",
            json={"messages": MSG},
            headers={"x-tenant-id": "acme", "x-request-id": "hc-1"},
        )
        client.post(
            "/serve",
            json={"messages": MSG},
            headers={"x-tenant-id": "random-stranger-42"},
        )
        reg.snapshots["worker_a"].kv_free_ratio = 0.01
        client.post("/serve", json={"messages": MSG}, headers={"x-request-priority": "batch"})
    text = client.get("/metrics").text
    assert 'orch_admit_total{class="interactive",decision="accept",reason="ok"}' in text
    assert 'orch_admit_total{class="batch",decision="shed",reason="kv_pressure"}' in text
    assert 'orch_shed_total{class="batch",code="503",reason="kv_pressure"}' in text
    for stage in ("guard", "admit", "place", "proxy"):
        assert (
            f'gateway_request_duration_seconds_count{{class="interactive",stage="{stage}"}}' in text
        )
    assert 'tenant="acme"' in text and 'tenant="other"' in text
    assert "random-stranger-42" not in text and "hc-1" not in text


# --- lifespan startup refresh --------------------------------------------------------


def test_lifespan_refreshes_before_first_request(monkeypatch) -> None:
    reg = Registry([Worker("worker_a", "http://worker-a:8000")])
    monkeypatch.setattr(gateway_main, "registry", reg)
    prom = (Path(__file__).parent / "fixtures" / "vllm-worker-b.prom").read_text()
    seen = []

    async def fake_get(self, url, **_):
        seen.append(url)
        return httpx.Response(200, text=prom, request=httpx.Request("GET", url))

    with patch("httpx.AsyncClient.get", fake_get):
        with TestClient(gateway_main.app) as client:
            assert reg.snapshots["worker_a"].healthy  # refreshed before serving
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
                post.side_effect = _ok
                r = client.post("/serve", json={"messages": MSG})
    assert r.status_code == 200 and seen


def test_lifespan_survives_failed_initial_refresh(monkeypatch) -> None:
    reg = Registry([Worker("worker_a", "http://worker-a:8000")])
    monkeypatch.setattr(gateway_main, "registry", reg)

    async def boom(self, url, **_):
        raise httpx.ConnectError("down")

    with patch("httpx.AsyncClient.get", boom):
        with TestClient(gateway_main.app) as client:
            assert client.get("/health").status_code == 200
            r = client.post("/serve", json={"messages": MSG})
    assert r.status_code == 503 and r.json()["error"] == "no_signal"


def test_flat_import_with_admission_modules() -> None:
    gateway_dir = Path(gateway_main.__file__).parent
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import main, admission, tenants; assert main.app.title",
        ],
        cwd=gateway_dir,
        check=True,
    )


def test_refund_returns_tokens_only_when_requested() -> None:
    q = TenantQuota(frozenset({"a"}), token_budget=100, window_s=10, max_concurrency=99)
    q.acquire("a", 60, now=0).release(refund=True)  # shed after quota: never ran
    assert not isinstance(q.acquire("a", 100, now=1), Shed)
    q2 = TenantQuota(frozenset({"a"}), token_budget=100, window_s=10, max_concurrency=99)
    q2.acquire("a", 60, now=0).release()  # served: tokens stay committed
    assert isinstance(q2.acquire("a", 60, now=1), Shed)
    lease = q2.acquire("a", 40, now=1)
    lease.release(refund=True)
    lease.release(refund=True)  # idempotent: no double refund
    assert not isinstance(q2.acquire("a", 40, now=2), Shed)
    assert isinstance(q2.acquire("a", 1, now=2), Shed)


def test_capacity_shed_does_not_burn_tenant_budget(gw) -> None:
    client, _ = gw
    q = gateway_main.quota
    hdrs = {"x-deadline-ms": "1", "x-tenant-id": "acme"}  # passes quota, then deadline shed
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        for _ in range(3):
            r = client.post("/serve", json={"messages": MSG}, headers=hdrs)
            assert r.status_code == 504
        post.assert_not_called()
    bucket = q._buckets[q.bucket_name("acme")]
    assert (bucket.tokens, bucket.active, len(bucket.window)) == (0, 0, 0)

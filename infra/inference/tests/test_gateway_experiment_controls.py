"""Test-only experiment control headers (#123 slice C): gated by ALLOW_EXPERIMENT_CONTROLS=1."""

import logging
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway.admission import AdmitConfig
from infra.inference.gateway.tenants import TenantQuota
from infra.inference.gateway.workers import Registry, Worker

MSG = [{"role": "user", "content": "hello"}]
CFG = AdmitConfig(
    max_decode_slots=4,
    kv_free_min=0.10,
    prefill_tokens_per_s=1000.0,
    queue_wait_per_waiting_s=0.5,
    stale_after_s=5.0,
)


@pytest.fixture
def gw(monkeypatch):
    reg = Registry(
        [Worker("worker_a", "http://worker-a:8000"), Worker("worker_b", "http://worker-b:8000")]
    )
    for s in reg.snapshots.values():
        s.healthy, s.observed_at, s.kv_free_ratio = True, time.monotonic(), 0.9
    monkeypatch.setattr(gateway_main, "registry", reg)
    monkeypatch.setattr(gateway_main, "ADMIT_CFG", CFG)
    monkeypatch.setattr(gateway_main, "PLACEMENT_POLICY", "least_loaded")
    monkeypatch.setattr(gateway_main, "quota", TenantQuota(frozenset({"acme"}), max_concurrency=1))
    return TestClient(gateway_main.app), reg


def _ok(url, **_):
    return httpx.Response(200, json={"choices": []})


def _post(client, headers=None):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        post.side_effect = _ok
        return client.post("/serve", json={"messages": MSG}, headers=headers or {}), post


def test_overrides_ignored_when_env_off(gw, monkeypatch) -> None:
    client, reg = gw
    monkeypatch.delenv("ALLOW_EXPERIMENT_CONTROLS", raising=False)
    r, _ = _post(client, {"x-admission-mode": "off", "x-placement-policy-override": "p2c"})
    assert r.status_code == 200 and r.headers["x-placement-policy"] == "least_loaded"
    assert "x-policy-override-applied" not in r.headers
    assert "x-admission-mode" not in r.headers
    # with every worker saturated the header must not bypass shedding
    for s in reg.snapshots.values():
        s.running = 4
    r, post = _post(client, {"x-admission-mode": "off"})
    assert r.status_code == 503 and r.json()["error"] == "decode_slots"
    post.assert_not_called()
    r, _ = _post(client, {"x-placement-policy-override": "garbage"})  # not even validated
    assert r.status_code == 503


def test_admission_off_admits_request_that_would_be_shed(gw, monkeypatch) -> None:
    client, reg = gw
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    for s in reg.snapshots.values():
        s.running = 4
    r, _ = _post(client)
    assert r.status_code == 503 and r.headers["x-admit-decision"].startswith("shed:")
    r, post = _post(client, {"x-admission-mode": "off"})
    assert r.status_code == 200
    assert r.headers["x-admission-mode"] == "off"
    assert r.headers["x-admit-decision"] == "accept"
    post.assert_called_once()
    r, _ = _post(client, {"x-admission-mode": "on"})
    assert r.status_code == 503 and r.headers["x-admission-mode"] == "on"


def test_admission_off_keeps_guard_and_tenant_quota(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    off = {"x-admission-mode": "off"}
    r = client.post("/serve", json={"messages": []}, headers=off)
    assert r.status_code == 400 and r.headers["x-guard-decision"] == "reject:missing_messages"
    lease = gateway_main.quota.acquire("acme", 1, time.monotonic())  # holds acme's only slot
    r, _ = _post(client, {**off, "x-tenant-id": "acme"})
    assert r.status_code == 429 and r.json()["error"] == "tenant_concurrency"
    lease.release()
    quota_off = {**off, "x-tenant-id": "acme", "x-tenant-quota-mode": "off"}
    lease = gateway_main.quota.acquire("acme", 1, time.monotonic())
    r, _ = _post(client, quota_off)
    assert r.status_code == 200
    lease.release()


def test_policy_override_changes_placement_for_same_trace(gw, monkeypatch) -> None:
    client, reg = gw
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    reg.snapshots["worker_a"].running = 2  # least_loaded prefers B; round_robin starts at A
    r, _ = _post(client)
    assert r.headers["x-place-decision"] == "worker_b"
    assert r.headers["x-placement-policy"] == "least_loaded"
    assert "x-policy-override-applied" not in r.headers
    reg._rr = 0
    r, _ = _post(client, {"x-placement-policy-override": "round_robin"})
    assert r.headers["x-placement-policy"] == "round_robin"
    assert r.headers["x-policy-override-applied"] == "round_robin"
    assert r.headers["x-place-decision"] == "worker_a"


def test_invalid_override_rejected_with_bounded_reason(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    for h in ({"x-placement-policy-override": "nope"}, {"x-admission-mode": "maybe"}):
        r, post = _post(client, h)
        assert r.status_code == 400 and r.json()["error"] == "invalid_experiment_control"
        assert "nope" not in r.text and "maybe" not in r.text
        post.assert_not_called()


def test_overrides_recorded_in_decision_log(gw, monkeypatch, caplog) -> None:
    client, _ = gw
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    with caplog.at_level(logging.INFO, logger="inference.gateway"):
        _post(client, {"x-placement-policy-override": "p2c", "x-admission-mode": "off"})
    assert any(
        '"policy_override": "p2c"' in m and '"admission_mode": "off"' in m for m in caplog.messages
    )


def test_forced_worker_still_wins_over_override(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    monkeypatch.setenv("ALLOW_FORCED_PLACEMENT", "1")
    r, _ = _post(
        client, {"x-force-worker": "worker_b", "x-placement-policy-override": "round_robin"}
    )
    assert (
        r.headers["x-place-decision"] == "worker_b" and r.headers["x-placement-policy"] == "forced"
    )

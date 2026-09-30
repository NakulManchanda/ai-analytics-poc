"""Overflow policy tests (#122 slice 4)."""

import asyncio
import logging
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway import metrics
from infra.inference.gateway.overflow import Local, Overflow, OverflowConfig, decide
from infra.inference.gateway.queueing import QueueConfig, WorkerQueues
from infra.inference.gateway.tenants import TenantQuota
from infra.inference.gateway.workers import Registry, Worker

MSG = [{"role": "user", "content": "hello"}]
KEY = "sk-test-SECRET-123"
OVERFLOW_URL = "https://overflow.example.test/v1/chat/completions"
CFG = OverflowConfig(True, "acme-cloud", "big-model", OVERFLOW_URL, KEY)

# (code, reason, never_overflow, source, expected_overflow) -- the issue's overflow table
# plus every gateway shed reason.
ELIGIBILITY = [
    (429, "tenant_tokens", True, "admit", False),
    (429, "tenant_concurrency", True, "admit", False),
    (429, "rate", False, "admit", False),  # 429 is never eligible even if flag missing
    (500, "internal", False, "upstream", False),
    (502, "proxy_error", False, "upstream", False),
    (504, "deadline_unachievable", False, "admit", False),
    (400, "prompt_too_large", False, "guard", False),
    (422, "prompt_injection", False, "guard", False),
    (503, "slice_oom", False, "upstream", False),
    (503, "unknown_forced_worker", False, "place", False),
    (503, "unknown_policy", False, "place", False),
    (503, "no_signal", False, "admit", True),
    (503, "kv_pressure", False, "admit", True),
    (503, "decode_slots", False, "admit", True),
    (503, "no_healthy_worker", False, "place", True),
    (503, "queue_full", False, "queue", True),
    (503, "timeout_queue", False, "queue", True),
    (503, "worker_unavailable", False, "upstream", True),
    (503, "worker_overloaded", False, "upstream", True),
    (529, "worker_overloaded", False, "upstream", True),
    (503, "no_signal", True, "admit", False),  # never_overflow wins
]


@pytest.mark.parametrize("code,reason,never,source,expected", ELIGIBILITY)
def test_decide_table(code, reason, never, source, expected) -> None:
    d = decide(code, reason, never, source)
    assert isinstance(d, Overflow if expected else Local)
    if expected:
        assert (d.original_reason, d.original_code) == (reason, code)


def test_config_from_env(monkeypatch) -> None:
    for k in ("ENABLED", "PROVIDER", "MODEL", "URL", "API_KEY"):
        monkeypatch.delenv(f"OVERFLOW_{k}", raising=False)
    assert not OverflowConfig.from_env().active  # default off
    monkeypatch.setenv("OVERFLOW_ENABLED", "1")
    assert not OverflowConfig.from_env().active  # misconfigured: no destination
    monkeypatch.setenv("OVERFLOW_PROVIDER", "p")
    monkeypatch.setenv("OVERFLOW_MODEL", "m")
    monkeypatch.setenv("OVERFLOW_URL", OVERFLOW_URL)
    monkeypatch.setenv("OVERFLOW_API_KEY", KEY)
    cfg = OverflowConfig.from_env()
    assert cfg.active and cfg.destination == "p/m"
    assert cfg.headers()["authorization"] == f"Bearer {KEY}"
    assert KEY not in repr(cfg.destination)


# --- gateway e2e ------------------------------------------------------------------------


@pytest.fixture
def gw(monkeypatch):
    reg = Registry([Worker("worker_a", "http://worker-a:8000")])
    s = reg.snapshots["worker_a"]
    s.healthy, s.observed_at = True, time.monotonic()
    monkeypatch.setattr(gateway_main, "registry", reg)
    monkeypatch.setattr(
        gateway_main,
        "queues",
        WorkerQueues(QueueConfig(max_inflight=1, max_depth=8, timeout_s=0.05)),
    )
    monkeypatch.setattr(gateway_main, "OVERFLOW_CFG", CFG)
    return TestClient(gateway_main.app), reg


class Calls:
    """Fake httpx.AsyncClient.post: routes by URL, records calls."""

    def __init__(self, local, overflow):
        self.local, self.overflow, self.urls, self.bodies, self.headers = (
            local,
            overflow,
            [],
            [],
            [],
        )
        self.timeouts, self.local_delay = [], 0.0

    async def __call__(self, client, url, **kw):
        self.urls.append(url)
        self.bodies.append(kw.get("json"))
        self.headers.append(kw.get("headers"))
        self.timeouts.append(client.timeout.read)
        if url != OVERFLOW_URL and self.local_delay:
            await asyncio.sleep(self.local_delay)
        out = self.overflow if url == OVERFLOW_URL else self.local
        if isinstance(out, Exception):
            raise out
        return out

    @property
    def overflow_calls(self):
        return self.urls.count(OVERFLOW_URL)


def serve(client, **kw):
    return client.post("/serve", json={"messages": MSG, "model": "local", **kw})


def metric_text() -> str:
    return metrics.render()[0].decode()


OFLOW_OK = httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})


@pytest.mark.parametrize("status", [503, 529])
def test_upstream_status_overflows(gw, status) -> None:
    client, reg = gw
    calls = Calls(httpx.Response(status, text="busy"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == 200 and r.json()["choices"]
    assert r.headers["x-overflow"] == "acme-cloud/big-model"
    assert r.headers["x-overflow-reason"] == "worker_overloaded"
    assert r.headers["x-place-decision"] == "overflow"
    assert calls.overflow_calls == 1
    assert calls.bodies[-1]["model"] == "big-model"
    assert calls.headers[-1]["authorization"] == f"Bearer {KEY}"
    assert gateway_main.queues.inflight("worker_a") == 0 and reg.snapshots["worker_a"].inflight == 0
    text = metric_text()
    assert (
        'orch_overflow_total{model="big-model",outcome="ok",provider="acme-cloud",'
        'reason="worker_overloaded"}'
    ) in text
    assert KEY not in text and KEY not in str(dict(r.headers))


def _bind(calls):
    async def post(self, url, **kw):
        return await calls(self, url, **kw)

    return post


def test_connect_error_overflows(gw) -> None:
    client, _ = gw
    calls = Calls(httpx.ConnectError("refused"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == 200 and r.headers["x-overflow-reason"] == "worker_unavailable"
    assert calls.overflow_calls == 1


def test_admission_shed_overflows(gw) -> None:
    client, reg = gw
    reg.snapshots["worker_a"].healthy = False  # -> no_signal
    calls = Calls(httpx.Response(200, json={}), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == 200 and r.headers["x-overflow-reason"] == "no_signal"
    assert calls.urls == [OVERFLOW_URL]  # local worker never called


def test_queue_timeout_overflow_keeps_tokens_charged(gw, monkeypatch) -> None:
    client, _ = gw
    quota = TenantQuota(frozenset({"acme"}), max_concurrency=1)
    monkeypatch.setattr(gateway_main, "quota", quota)
    loop = asyncio.new_event_loop()
    held = loop.run_until_complete(gateway_main.queues.acquire("worker_a", "interactive", 1))
    calls = Calls(httpx.Response(200, json={}), OFLOW_OK)
    try:
        with patch.object(httpx.AsyncClient, "post", _bind(calls)):
            r = client.post("/serve", json={"messages": MSG}, headers={"x-tenant-id": "acme"})
    finally:
        held.release()
        loop.close()
    assert r.status_code == 200 and r.headers["x-overflow-reason"] == "timeout_queue"
    assert calls.urls == [OVERFLOW_URL]
    b = quota._buckets["acme"]
    assert b.tokens > 0 and b.active == 0  # overflow served it: charged, lease released


def test_disabled_is_unchanged(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setattr(gateway_main, "OVERFLOW_CFG", OverflowConfig())
    calls = Calls(httpx.ConnectError("refused"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == 503 and "x-overflow" not in r.headers
    assert calls.overflow_calls == 0


def test_enabled_but_misconfigured_is_unchanged(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setattr(gateway_main, "OVERFLOW_CFG", OverflowConfig(True, "p", "", "", KEY))
    calls = Calls(httpx.ConnectError("refused"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == 503 and calls.overflow_calls == 0


def test_429_never_overflows(gw, monkeypatch) -> None:
    client, _ = gw
    monkeypatch.setattr(gateway_main, "quota", TenantQuota(frozenset({"acme"}), token_budget=1))
    calls = Calls(httpx.Response(200, json={}), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = client.post("/serve", json={"messages": MSG}, headers={"x-tenant-id": "acme"})
    assert r.status_code == 429 and calls.urls == []


@pytest.mark.parametrize("status", [500, 502, 400])
def test_non_capacity_status_stays_local(gw, status) -> None:
    client, _ = gw
    calls = Calls(httpx.Response(status, text="bad"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == status and calls.overflow_calls == 0


def test_deadline_unachievable_stays_local(gw) -> None:
    client, _ = gw
    calls = Calls(httpx.Response(200, json={}), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = client.post(
            "/serve",
            json={"messages": MSG},
            headers={"x-deadline-ms": "1", "x-estimated-prompt-tokens": "3000"},
        )
    assert r.status_code == 504 and calls.urls == []


def test_guard_reject_stays_local(gw) -> None:
    client, _ = gw
    calls = Calls(httpx.Response(200, json={}), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = client.post("/serve", json={"nope": 1})
    assert 400 <= r.status_code < 500 and calls.urls == []


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.Response(500, text="no")])
def test_overflow_failure_returns_original_error_once(gw, failure, caplog) -> None:
    client, _ = gw
    calls = Calls(httpx.Response(503, text="busy"), failure)
    caplog.set_level(logging.INFO)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.status_code == 503 and "x-overflow" not in r.headers
    assert (
        calls.overflow_calls == 1
        and calls.urls.count("http://worker-a:8000/v1/chat/completions") == 1
    )
    kind = "fallback_error" if isinstance(failure, httpx.ConnectError) else "fallback_5xx"
    assert f'overflow_error_total{{reason="{kind}"}}' in metric_text()
    assert 'orch_overflow_total{model="big-model",outcome="error"' in metric_text()
    assert KEY not in caplog.text and KEY not in metric_text()
    assert "acme-cloud" in caplog.text  # destination is logged, secret is not


class _Stream:
    def __init__(self, status, lines=()):
        self.status_code, self._lines = status, lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"busy"


def test_streaming_overflow_when_local_503_before_bytes(gw) -> None:
    client, _ = gw
    seen = []

    def fake_stream(self, method, url, **kw):
        seen.append((url, kw["json"]["model"]))
        return _Stream(503) if url != OVERFLOW_URL else _Stream(200, ["data: from-overflow"])

    with patch.object(httpx.AsyncClient, "stream", fake_stream):
        r = client.post("/serve", json={"messages": MSG, "model": "local", "stream": True})
    assert r.status_code == 200 and "data: from-overflow" in r.text
    assert r.headers["x-overflow"] == "acme-cloud/big-model"
    assert [u for u, _ in seen] == ["http://worker-a:8000/v1/chat/completions", OVERFLOW_URL]
    assert seen[1][1] == "big-model"
    assert gateway_main.queues.inflight("worker_a") == 0


def test_no_overflow_once_local_stream_started(gw) -> None:
    client, _ = gw

    def fake_stream(self, method, url, **kw):
        assert url != OVERFLOW_URL
        return _Stream(200, ["data: local"])

    with patch.object(httpx.AsyncClient, "stream", fake_stream):
        r = client.post("/serve", json={"messages": MSG, "stream": True})
    assert r.status_code == 200 and "x-overflow" not in r.headers and "data: local" in r.text


def sample(name: str, **labels) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


def test_upstream_503_then_overflow_counts_only_final_200(gw) -> None:
    client, _ = gw

    def now(status: str) -> float:
        return sample("gateway_requests_total", status=status, **{"class": "interactive"})

    b503, b200 = now("503"), now("200")
    calls = Calls(httpx.Response(503, text="busy"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        assert serve(client).status_code == 200
    assert now("503") == b503 and now("200") == b200 + 1


def test_overflow_failure_counts_original_503_once(gw) -> None:
    client, _ = gw
    key = dict(status="503", **{"class": "interactive"})
    before = sample("gateway_requests_total", **key)
    calls = Calls(httpx.Response(503, text="busy"), httpx.ConnectError("x"))
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        assert serve(client).status_code == 503
    assert sample("gateway_requests_total", **key) == before + 1


@pytest.mark.parametrize(
    "failure,kind",
    [
        (httpx.ReadTimeout("t"), "fallback_timeout"),
        (httpx.Response(502), "fallback_5xx"),
        (httpx.Response(401), "fallback_error"),
    ],
)
def test_overflow_error_classified_by_fallback_failure(gw, failure, kind) -> None:
    client, _ = gw
    before = sample("overflow_error_total", reason=kind)
    calls = Calls(httpx.Response(503, text="busy"), failure)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        assert serve(client).status_code == 503
    assert sample("overflow_error_total", reason=kind) == before + 1


def test_overflow_capped_to_remaining_deadline(gw) -> None:
    client, _ = gw
    calls = Calls(httpx.Response(503, text="busy"), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = client.post("/serve", json={"messages": MSG}, headers={"x-deadline-ms": "5000"})
    assert r.status_code == 200
    assert 0 < calls.timeouts[-1] <= 5.0 < CFG.timeout_s  # min(OVERFLOW_TIMEOUT_S, remaining)


def test_overflow_skipped_when_deadline_spent(gw) -> None:
    client, _ = gw
    before = sample("overflow_error_total", reason="no_time_remaining")
    calls = Calls(httpx.Response(503, text="busy"), OFLOW_OK)
    calls.local_delay = 0.15
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = client.post("/serve", json={"messages": MSG}, headers={"x-deadline-ms": "100"})
    assert r.status_code == 503 and calls.overflow_calls == 0 and "x-overflow" not in r.headers
    assert sample("overflow_error_total", reason="no_time_remaining") == before + 1


class _BrokenStream(_Stream):
    async def aiter_lines(self):
        yield "data: partial"
        raise httpx.ReadError("boom")


def test_streaming_overflow_error_midway_is_not_ok(gw) -> None:
    client, _ = gw
    ok = dict(reason="no_signal", provider="acme-cloud", model="big-model")
    ok_before = sample("orch_overflow_total", outcome="ok", **ok)
    err_before = sample("orch_overflow_total", outcome="error", **ok)
    e_before = sample("overflow_error_total", reason="fallback_error")
    gateway_main.registry.snapshots["worker_a"].healthy = False  # -> no_signal -> overflow

    with patch.object(httpx.AsyncClient, "stream", lambda *a, **k: _BrokenStream(200)):
        r = client.post("/serve", json={"messages": MSG, "stream": True})
    assert "data: partial" in r.text and "overflow_stream_error" in r.text
    assert "boom" not in r.text
    assert sample("orch_overflow_total", outcome="ok", **ok) == ok_before
    assert sample("orch_overflow_total", outcome="error", **ok) == err_before + 1
    assert sample("overflow_error_total", reason="fallback_error") == e_before + 1


def test_overflow_holds_tenant_lease_until_stream_completes(gw, monkeypatch) -> None:
    _, reg = gw
    reg.snapshots["worker_a"].healthy = False  # every request sheds no_signal -> overflows
    quota = TenantQuota(frozenset({"acme"}), max_concurrency=1)
    monkeypatch.setattr(gateway_main, "quota", quota)
    hdr = {"x-tenant-id": "acme"}

    class Held(_Stream):
        def __init__(self, started, go):
            super().__init__(200)
            self.started, self.go = started, go

        async def aiter_lines(self):
            self.started.set()
            await self.go.wait()
            yield "data: done"

    started, go = asyncio.Event(), asyncio.Event()

    def fake_stream(self, *a, **k):
        return Held(started, go)

    async def run():
        transport = httpx.ASGITransport(app=gateway_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
            first = asyncio.create_task(
                ac.post("/serve", json={"messages": MSG, "stream": True}, headers=hdr)
            )
            await started.wait()
            second = await ac.post("/serve", json={"messages": MSG}, headers=hdr)
            go.set()
            return await first, second

    with patch.object(httpx.AsyncClient, "stream", fake_stream):
        first, second = asyncio.run(run())
    assert second.status_code == 429 and second.json()["error"] == "tenant_concurrency"
    assert first.status_code == 200 and "data: done" in first.text
    b = quota._buckets["acme"]
    assert b.active == 0 and b.tokens > 0  # lease freed at completion; tokens stay charged


# --- gateway TTFT histogram (#123 review follow-up) --------------------------------------


def ttft_count(klass: str) -> float:
    return sample("gateway_ttft_seconds_count", **{"class": klass})


ROLE = 'data: {"choices":[{"delta":{"role":"assistant"}}]}'
TOKEN = 'data: {"choices":[{"delta":{"content":"Hi"}}]}'
TEXT = 'data: {"choices":[{"text":"Hi"}]}'
EMPTY = 'data: {"choices":[{"delta":{"content":""}}]}'
NOISE = [": keepalive", "data: not-json", 'data: {"error":"boom"}', "data: [DONE]", ROLE, EMPTY]


class _Spy(_Stream):
    """Records which lines were emitted at the moment TTFT is observed."""

    emitted: list

    async def aiter_lines(self):
        for line in self._lines:
            self.emitted.append(line)
            yield line


def _stream_ttft(client, lines, local=True):
    """Post a streaming request; return the emitted-lines snapshots at each TTFT observation."""
    emitted, seen = [], []

    class Spy(_Spy):
        pass

    Spy.emitted = emitted

    class Obs:
        def observe(self, _v):
            seen.append(list(emitted))

    def fake_stream(self, method, url, **kw):
        if local and url != OVERFLOW_URL:
            return Spy(200, lines)
        return _Stream(503) if url != OVERFLOW_URL else Spy(200, lines)

    with (
        patch.object(httpx.AsyncClient, "stream", fake_stream),
        patch.object(metrics.TTFT, "labels", lambda *a: Obs()),
    ):
        r = client.post("/serve", json={"messages": MSG, "stream": True})
    assert r.status_code == 200
    return seen


@pytest.mark.parametrize("local", [True, False])
def test_ttft_waits_for_first_content_chunk(gw, local) -> None:
    client, _ = gw
    lines = [ROLE, ": keepalive", "data: not-json", EMPTY, TOKEN, "data: more", "data: [DONE]"]
    seen = _stream_ttft(client, lines, local)
    assert len(seen) == 1 and seen[0][-1] == TOKEN  # not at the role/keepalive record
    assert seen[0] == lines[:5]  # observed at the content chunk, before later chunks


def test_ttft_accepts_completions_text_chunks(gw) -> None:
    client, _ = gw
    seen = _stream_ttft(client, [ROLE, TEXT], True)
    assert len(seen) == 1 and seen[0][-1] == TEXT


@pytest.mark.parametrize("local", [True, False])
def test_ttft_not_observed_without_content(gw, local) -> None:
    client, _ = gw
    assert _stream_ttft(client, NOISE, local) == []


def test_nonstreaming_requests_absent_from_ttft_series(gw) -> None:
    client, _ = gw
    before = {k: ttft_count(k) for k in ("interactive", "batch")}
    calls = Calls(httpx.Response(200, json={"choices": []}), OFLOW_OK)
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = client.post("/serve", json={"messages": MSG}, headers={"x-request-priority": "batch"})
    assert r.status_code == 200
    calls = Calls(httpx.Response(503, text="busy"), OFLOW_OK)  # non-streaming overflow 200
    with patch.object(httpx.AsyncClient, "post", _bind(calls)):
        r = serve(client)
    assert r.headers["x-overflow"]
    assert {k: ttft_count(k) for k in before} == before

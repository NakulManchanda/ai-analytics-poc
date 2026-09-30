from __future__ import annotations

import json

import httpx
import pytest
from app.benchmarks.replayer import (
    ScenarioReplayer,
    calculate_percentiles,
)
from app.scenarios.models import (
    ScenarioConfig,
    ScenarioConversation,
    ScenarioTurn,
)


def test_calculate_percentiles():
    assert calculate_percentiles([]) == {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    single = calculate_percentiles([100.0])
    assert single["p50"] == 100.0

    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    p = calculate_percentiles(values)
    assert p["p50"] == 55.0
    assert p["p90"] == 91.0
    assert p["p99"] == 99.1


@pytest.mark.anyio
async def test_replayer_sse_path():
    cfg = ScenarioConfig(
        name="test_sse_replay",
        description="Test SSE replayer path",
        concurrency=1,
        strategy="manual",
        target_endpoint_type="app_runs",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="c1",
                turns=[
                    ScenarioTurn(question="Which pickup zones have the most trips?"),
                    ScenarioTurn(
                        question="What are the peak hours and busiest times for taxi rides in NYC?"
                    ),
                ],
            )
        ],
    )

    known_conversations: set[str] = set()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            body = json.loads(request.content.decode("utf-8"))
            client_conv_id = body.get("conversation_id")
            if client_conv_id is not None:
                if client_conv_id not in known_conversations:
                    return httpx.Response(
                        404,
                        json={"detail": {"code": "conversation_not_found"}},
                    )
                conv_id = client_conv_id
            else:
                conv_id = f"server_conv_{len(known_conversations) + 1}"
                known_conversations.add(conv_id)

            run_id = f"run_{hash(body['prompt']) & 0xffff}"
            return httpx.Response(
                202,
                json={
                    "conversation_id": conv_id,
                    "message_id": "msg_123",
                    "run_id": run_id,
                    "events_url": f"/api/runs/{run_id}/events",
                },
            )
        elif request.method == "GET" and "/events" in url_str:
            completed_envelope = json.dumps(
                {
                    "event_id": "evt_3",
                    "event_type": "run.completed",
                    "run_id": "run_test",
                    "conversation_id": "server_conv_1",
                    "sequence": 3,
                    "payload": {
                        "status": "completed",
                        "input_tokens": 150,
                        "output_tokens": 50,
                        "telemetry": {"ttft_ms": 45.2},
                    },
                }
            )
            received_str = (
                '{"event_type": "run.received", "payload": {"status": "in_progress"}}'
            )
            delta_str = (
                '{"event_type": "answer.delta", "payload": {"delta": "Top zones..."}}'
            )
            sse_content = (
                f"event: run.received\ndata: {received_str}\n\n"
                f"event: answer.delta\ndata: {delta_str}\n\n"
                f"event: run.completed\ndata: {completed_envelope}\n\n"
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=sse_content,
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        replayer = ScenarioReplayer(
            config=cfg,
            target_base_url="http://mock-app:8080",
            client=client,
            timeout=5.0,
            use_sse=True,
        )
        summary = await replayer.run()

        assert summary.total_conversations == 1
        assert summary.total_turns == 2
        assert summary.successful_turns == 2
        assert summary.failed_turns == 0
        assert summary.total_prompt_tokens == 300
        assert summary.total_completion_tokens == 100
        assert summary.ttft_ms["p50"] == pytest.approx(45.2)
        assert len(known_conversations) == 1
        assert "server_conv_1" in known_conversations


@pytest.mark.anyio
async def test_replayer_polling_fallback():
    cfg = ScenarioConfig(
        name="test_poll_fallback",
        description="Test polling fallback",
        concurrency=1,
        strategy="manual",
        target_endpoint_type="app_runs",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="poll_c1",
                turns=[
                    ScenarioTurn(question="Which pickup zones have the most trips?")
                ],
            )
        ],
    )

    known_conversations: set[str] = set()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            body = json.loads(request.content.decode("utf-8"))
            client_conv_id = body.get("conversation_id")
            if client_conv_id is not None:
                if client_conv_id not in known_conversations:
                    return httpx.Response(
                        404,
                        json={"detail": {"code": "conversation_not_found"}},
                    )
                conv_id = client_conv_id
            else:
                conv_id = "poll_c1"
                known_conversations.add(conv_id)

            return httpx.Response(
                202,
                json={
                    "conversation_id": conv_id,
                    "message_id": "msg_poll",
                    "run_id": "run_poll_1",
                    "events_url": "/api/runs/run_poll_1/events",
                },
            )
        elif request.method == "GET" and "/events" in url_str:
            # SSE fails with 500 to trigger fallback
            return httpx.Response(500, text="SSE server error")
        elif request.method == "GET" and "/api/conversations/" in url_str:
            return httpx.Response(
                200,
                json={
                    "conversation_id": "poll_c1",
                    "created_at": "2026-09-30T00:00:00Z",
                    "updated_at": "2026-09-30T00:00:00Z",
                    "title": None,
                    "messages": [],
                    "runs": [
                        {
                            "run_id": "run_poll_1",
                            "message_id": "msg_poll",
                            "status": "completed",
                            "started_at": "2026-09-30T00:00:00Z",
                            "completed_at": "2026-09-30T00:00:01Z",
                            "input_tokens": 120,
                            "output_tokens": 40,
                            "estimated_cost_usd": 0.0001,
                            "steps": [],
                        }
                    ],
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        replayer = ScenarioReplayer(
            config=cfg,
            target_base_url="http://mock-app:8080",
            client=client,
            use_sse=True,
        )
        summary = await replayer.run()
        assert summary.successful_turns == 1
        assert summary.total_prompt_tokens == 120
        assert summary.total_completion_tokens == 40


@pytest.mark.anyio
async def test_replayer_gateway_chat_path():
    cfg = ScenarioConfig(
        name="test_gateway_direct",
        description="Direct chat completion",
        concurrency=1,
        strategy="manual",
        target_endpoint_type="gateway_chat",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="gw_c1",
                turns=[
                    ScenarioTurn(question="Which pickup zones have the most trips?")
                ],
            )
        ],
    )

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/v1/chat/completions"):
            assert request.headers.get("x-conversation-id").startswith("gw_c1")
            assert request.headers.get("x-agent-step") == "1"
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-123",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 80},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        replayer = ScenarioReplayer(
            config=cfg,
            target_base_url="http://mock-gateway:18080",
            client=client,
        )
        summary = await replayer.run()
        assert summary.successful_turns == 1
        assert summary.total_prompt_tokens == 200
        assert summary.total_completion_tokens == 80


@pytest.mark.anyio
async def test_replayer_multiline_sse_payload():
    cfg = ScenarioConfig(
        name="test_multiline_sse",
        description="Multi-line SSE payload",
        concurrency=1,
        strategy="manual",
        target_endpoint_type="app_runs",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="ml_c1",
                turns=[
                    ScenarioTurn(question="Which pickup zones have the most trips?")
                ],
            )
        ],
    )

    known_conversations: set[str] = set()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            body = json.loads(request.content.decode("utf-8"))
            client_conv_id = body.get("conversation_id")
            if client_conv_id is not None:
                if client_conv_id not in known_conversations:
                    return httpx.Response(
                        404,
                        json={"detail": {"code": "conversation_not_found"}},
                    )
                conv_id = client_conv_id
            else:
                conv_id = "ml_c1"
                known_conversations.add(conv_id)

            return httpx.Response(
                202,
                json={
                    "conversation_id": conv_id,
                    "message_id": "msg_ml",
                    "run_id": "run_ml_1",
                    "events_url": "/api/runs/run_ml_1/events",
                },
            )
        elif request.method == "GET" and "/events" in url_str:
            # Multi-line SSE data payload with line breaks inside JSON
            sse_content = (
                "event: answer.delta\n"
                "data: {\n"
                'data:   "event_type": "answer.delta",\n'
                'data:   "payload": {"delta": "hello\\nworld"}\n'
                "data: }\n\n"
                "event: run.completed\n"
                "data: {\n"
                'data:   "event_type": "run.completed",\n'
                'data:   "payload": {\n'
                'data:     "status": "completed",\n'
                'data:     "input_tokens": 100,\n'
                'data:     "output_tokens": 25,\n'
                'data:     "telemetry": {"ttft_ms": 30.0}\n'
                "data:   }\n"
                "data: }\n\n"
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=sse_content,
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        replayer = ScenarioReplayer(
            config=cfg,
            target_base_url="http://mock-app:8080",
            client=client,
            timeout=5.0,
            use_sse=True,
        )
        summary = await replayer.run()
        assert summary.successful_turns == 1
        assert summary.total_prompt_tokens == 100
        assert summary.total_completion_tokens == 25
        assert summary.ttft_ms["p50"] == pytest.approx(30.0)


@pytest.mark.anyio
async def test_replayer_never_sends_synthetic_id_after_first_turn_failure():
    cfg = ScenarioConfig(
        name="test_first_turn_failure",
        description="First turn fails before a server conversation_id exists",
        concurrency=1,
        strategy="manual",
        target_endpoint_type="app_runs",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="c1",
                turns=[
                    ScenarioTurn(question="Which pickup zones have the most trips?"),
                    ScenarioTurn(question="What are the peak hours?"),
                ],
            )
        ],
    )
    sent_ids: list[object] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        sent_ids.append(json.loads(request.content).get("conversation_id"))
        return httpx.Response(500, json={"detail": "boom"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        replayer = ScenarioReplayer(
            config=cfg,
            target_base_url="http://mock-app:8080",
            client=client,
            timeout=5.0,
            use_sse=True,
        )
        summary = await replayer.run()

    assert summary.failed_turns == 2
    assert sent_ids == [None, None]


@pytest.mark.anyio
async def test_replayer_gateway_records_headers_and_timing():
    cfg = ScenarioConfig(
        name="gw_records",
        description="records",
        target_endpoint_type="gateway_chat",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="r",
                tenant_id="t1",
                turns=[
                    ScenarioTurn(
                        question="q1",
                        workload_class="batch",
                        deadline_ms=2000,
                        prefix_id="pfx",
                    ),
                    ScenarioTurn(question="q2"),
                ],
            )
        ],
    )
    seen: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        if len(seen) == 2:
            return httpx.Response(
                429, headers={"x-admit-decision": "shed"}, text="shed"
            )
        return httpx.Response(
            200,
            headers={"x-place-decision": "w1", "x-queue-wait-ms": "12"},
            json={"id": "x", "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        summary = await ScenarioReplayer(cfg, "http://gw", client=client).run()

    h0 = seen[0]
    assert h0["x-request-priority"] == "batch"
    assert h0["x-tenant-id"] == "t1"
    assert h0["x-deadline-ms"] == "2000"
    assert h0["x-prefix-id"] == "pfx"
    assert "x-request-priority" not in seen[1]
    r0, r1 = summary.turn_results
    assert r0.gateway_headers == {"x-place-decision": "w1", "x-queue-wait-ms": "12"}
    assert (r0.workload_class, r0.tenant_id, r0.deadline_ms) == ("batch", "t1", 2000)
    assert r0.ended_at >= r0.started_at > 0
    assert r1.status == "http_429"
    assert r1.gateway_headers == {"x-admit-decision": "shed"}
    assert r1.workload_class == "interactive"


@pytest.mark.anyio
async def test_replayer_app_runs_leaves_gateway_headers_empty():
    cfg = ScenarioConfig(
        name="app_none",
        description="d",
        conversations=[
            ScenarioConversation(turns=[ScenarioTurn(question="q", tenant_id="t")])
        ],
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        summary = await ScenarioReplayer(cfg, "http://app", client=client).run()
    r = summary.turn_results[0]
    assert r.gateway_headers is None
    assert r.tenant_id == "t"
    assert r.started_at is not None


def _gw_cfg() -> ScenarioConfig:
    return ScenarioConfig(
        name="gw_stream",
        description="d",
        target_endpoint_type="gateway_chat",
        conversations=[
            ScenarioConversation(turns=[ScenarioTurn(question="q")]),
        ],
    )


async def _run_gw(handler, **kw):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await ScenarioReplayer(_gw_cfg(), "http://gw", client=client, **kw).run()


_SSE = {"content-type": "text/event-stream", "x-place-decision": "w1"}


@pytest.mark.anyio
async def test_gateway_stream_success_measures_ttft_and_usage():
    body = (
        'data: {"id":"a","choices":[{"delta":{"role":"assistant"}}]}\n\n'
        'data: {"id":"a","choices":[{"delta":{"content":""}}]}\n\n'
        'data: {"id":"a","choices":[{"delta":{"content":"Hi"}}]}\n\n'
        'data: {"id":"a","choices":[{"delta":{"content":"!"}}],'
        '"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    seen = {}

    async def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, headers=_SSE, text=body)

    s = await _run_gw(handler)
    r = s.turn_results[0]
    assert seen["stream"] is True
    assert r.status == "completed"
    assert r.server_ttft_ms is not None and r.server_ttft_ms <= r.client_duration_ms
    assert (r.tokens_in, r.tokens_out) == (7, 2)
    assert r.gateway_headers == {"x-place-decision": "w1"}


@pytest.mark.anyio
async def test_gateway_stream_without_usage_falls_back_to_chunk_count():
    body = (
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request):
        return httpx.Response(200, headers=_SSE, text=body)

    r = (await _run_gw(handler)).turn_results[0]
    assert r.tokens_out == 2 and r.server_ttft_ms is not None


@pytest.mark.anyio
async def test_gateway_stream_error_status_captures_headers():
    async def handler(request):
        return httpx.Response(
            429,
            headers={"x-admit-decision": "shed", "x-overflow": "false"},
            json={"error": "shed"},
        )

    r = (await _run_gw(handler)).turn_results[0]
    assert r.status == "http_429"
    assert r.gateway_headers == {"x-admit-decision": "shed", "x-overflow": "false"}
    assert r.server_ttft_ms is None


@pytest.mark.anyio
async def test_gateway_stream_midstream_error_event_is_failure():
    body = (
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        'data: {"error": "upstream died"}\n\n'
    )

    async def handler(request):
        return httpx.Response(200, headers=_SSE, text=body)

    r = (await _run_gw(handler)).turn_results[0]
    assert r.status == "stream_error"
    assert "upstream died" in r.error


@pytest.mark.anyio
async def test_gateway_no_stream_mode_sends_no_stream_flag():
    seen = {}

    async def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "x", "usage": {}})

    r = (await _run_gw(handler, gateway_stream=False)).turn_results[0]
    assert "stream" not in seen
    assert r.status == "completed" and r.server_ttft_ms is None


@pytest.mark.anyio
async def test_goodput_from_streamed_ttft():
    from app.benchmarks.goodput import Slos, is_good

    body = 'data: {"choices":[{"delta":{"content":"a"}}]}\n\ndata: [DONE]\n\n'

    async def handler(request):
        return httpx.Response(200, headers=_SSE, text=body)

    r = (await _run_gw(handler)).turn_results[0]
    assert is_good(r, Slos())
    assert not is_good(r, Slos(interactive_ttft_slo_ms=0.0))

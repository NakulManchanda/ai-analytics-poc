from __future__ import annotations

import json

import httpx
import pytest
from app.benchmarks.replayer import (
    ControlNotApplied,
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
async def test_gateway_stream_without_usage_leaves_tokens_unmeasured():
    body = (
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request):
        return httpx.Response(200, headers=_SSE, text=body)

    r = (await _run_gw(handler)).turn_results[0]
    assert r.status == "completed" and r.server_ttft_ms is not None
    assert r.tokens_out is None and r.tokens_in is None


@pytest.mark.anyio
async def test_gateway_stream_premature_eof_is_incomplete_not_good():
    from app.benchmarks.goodput import Slos, is_good

    body = 'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'

    async def handler(request):
        return httpx.Response(200, headers=_SSE, text=body)

    r = (await _run_gw(handler)).turn_results[0]
    assert r.status == "incomplete_stream"
    assert not is_good(r, Slos())


@pytest.mark.anyio
async def test_gateway_request_id_sent_returned_and_stored():
    sent = {}

    async def handler(request):
        sent["id"] = request.headers["x-request-id"]
        return httpx.Response(
            200,
            headers={**_SSE, "x-request-id": request.headers["x-request-id"]},
            text="data: [DONE]\n\n",
        )

    r = (await _run_gw(handler)).turn_results[0]
    assert r.request_id == sent["id"]
    assert sent["id"].startswith("gw_stream-")


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


@pytest.mark.anyio
async def test_replay_summary_tokens_unavailable_when_unmeasured():
    body = 'data: {"choices":[{"delta":{"content":"a"}}]}\n\ndata: [DONE]\n\n'

    async def handler(request):
        return httpx.Response(200, headers=_SSE, text=body)

    summary = await _run_gw(handler)
    assert summary.total_prompt_tokens is None
    assert summary.total_completion_tokens is None
    assert summary.tokens_unmeasured_turns == 1
    assert summary.tokens_measured_turns == 0


@pytest.mark.anyio
async def test_gateway_stream_read_error_keeps_request_id_and_headers():
    class Boom(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            raise httpx.ReadError("connection reset")

    async def handler(request):
        return httpx.Response(
            200,
            headers={**_SSE, "x-request-id": "srv-id"},
            stream=Boom(),
        )

    r = (await _run_gw(handler)).turn_results[0]
    assert r.status == "client_exception"
    assert r.request_id == "srv-id"
    assert r.gateway_headers == {"x-place-decision": "w1"}


@pytest.mark.anyio
async def test_gateway_connect_error_keeps_generated_request_id():
    async def handler(request):
        raise httpx.ConnectError("down")

    r = (await _run_gw(handler)).turn_results[0]
    assert r.status == "client_exception"
    assert r.request_id and r.request_id.startswith("gw_stream-")
    assert r.gateway_headers is None


@pytest.mark.anyio
async def test_gateway_system_prefix_grows_history_and_sends_stable_prefix_id():
    cfg = ScenarioConfig(
        name="gw_prefix",
        description="d",
        target_endpoint_type="gateway_chat",
        system_prefix="SHARED RULES",
        policy_override="least_loaded",
        admission_mode="off",
        conversations=[
            ScenarioConversation(
                turns=[ScenarioTurn(question="q1"), ScenarioTurn(question="q2")]
            ),
            ScenarioConversation(turns=[ScenarioTurn(question="q3")]),
        ],
    )
    sent: list[tuple[list[dict], httpx.Headers]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append((body["messages"], request.headers))
        return httpx.Response(
            200,
            headers={
                "x-admission-mode": "off",
                "x-policy-override-applied": "least_loaded",
            },
            json={"choices": [{"message": {"content": f"a{len(sent)}"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        summary = await ScenarioReplayer(
            cfg, "http://gw", client=client, gateway_stream=False
        ).run()

    by_last = {m[-1]["content"]: (m, h) for m, h in sent}
    m1, h1 = by_last["q1"]
    m2, h2 = by_last["q2"]
    m3, h3 = by_last["q3"]
    assert m1 == [
        {"role": "system", "content": "SHARED RULES"},
        {"role": "user", "content": "q1"},
    ]
    assert m2[:3] == m1[:1] + [
        m1[1],
        {"role": "assistant", "content": m2[2]["content"]},
    ]
    assert m2[2]["content"].startswith("a") and len(m2) == 4  # grows: sys, q1, a, q2
    assert len(m3) == 2  # a new conversation does not inherit history
    assert h1["x-prefix-id"] == h2["x-prefix-id"] == h3["x-prefix-id"]
    assert len(h1["x-prefix-id"]) == 16
    # x-prefix-tokens sizes the system-prefix region only, constant across turns/conversations
    assert h1["x-prefix-tokens"] == h2["x-prefix-tokens"] == h3["x-prefix-tokens"]
    assert 0 < int(h1["x-prefix-tokens"]) < len(json.dumps(m2)) // 4
    assert h1["x-placement-policy-override"] == "least_loaded"
    assert h1["x-admission-mode"] == "off"
    r = summary.turn_results[0]
    assert (r.policy_override, r.admission_mode) == ("least_loaded", "off")
    assert r.gateway_headers == {
        "x-admission-mode": "off",
        "x-policy-override-applied": "least_loaded",
    }


@pytest.mark.anyio
async def test_gateway_without_prefix_or_controls_sends_bare_question():
    seen: list[tuple[dict, httpx.Headers]] = []

    async def handler(request):
        seen.append((json.loads(request.content), request.headers))
        return httpx.Response(200, json={"id": "x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ScenarioReplayer(
            _gw_cfg(), "http://gw", client=client, gateway_stream=False
        ).run()
    body, headers = seen[0]
    assert body["messages"] == [{"role": "user", "content": "q"}]
    for h in (
        "x-prefix-id",
        "x-prefix-tokens",
        "x-placement-policy-override",
        "x-admission-mode",
    ):
        assert h not in headers


@pytest.mark.anyio
async def test_gateway_stream_history_uses_streamed_text():
    cfg = _gw_cfg().model_copy(update={"system_prefix": "P"})
    cfg.conversations[0].turns.append(ScenarioTurn(question="q2"))
    bodies: list[list[dict]] = []
    sse = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request):
        bodies.append(json.loads(request.content)["messages"])
        return httpx.Response(200, headers=_SSE, text=sse)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ScenarioReplayer(cfg, "http://gw", client=client).run()
    assert bodies[1][2] == {"role": "assistant", "content": "Hello"}


def _ctl_cfg(**kw) -> ScenarioConfig:
    return _gw_cfg().model_copy(update=kw)


@pytest.mark.anyio
async def test_control_not_echoed_marks_turn_and_aborts_run():
    cfg = _ctl_cfg(policy_override="p2c")
    cfg.conversations[0].turns.append(ScenarioTurn(question="q2"))
    calls = []

    async def handler(request):
        calls.append(1)
        return httpx.Response(200, headers={"x-place-decision": "w1"}, json={"id": "x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ControlNotApplied):
            await ScenarioReplayer(
                cfg, "http://gw", client=client, gateway_stream=False
            ).run()
    assert len(calls) == 1  # aborted after the first response, second turn never sent


@pytest.mark.anyio
async def test_control_echo_mismatch_is_control_not_applied_turn():
    cfg = _ctl_cfg(admission_mode="off")

    async def handler(request):
        return httpx.Response(200, headers={"x-admission-mode": "on"}, json={"id": "x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # first response is verified before abort; run() raises rather than reporting success
        with pytest.raises(ControlNotApplied):
            await ScenarioReplayer(
                cfg, "http://gw", client=client, gateway_stream=False
            ).run()


@pytest.mark.anyio
async def test_matching_echo_is_verified_and_completed():
    cfg = _ctl_cfg(policy_override="p2c", admission_mode="on")

    async def handler(request):
        return httpx.Response(
            200,
            headers={"x-policy-override-applied": "p2c", "x-admission-mode": "on"},
            json={"id": "x"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        s = await ScenarioReplayer(
            cfg, "http://gw", client=client, gateway_stream=False
        ).run()
    assert s.turn_results[0].status == "completed"


@pytest.mark.anyio
async def test_control_lost_mid_run_marks_only_that_turn_failed():
    cfg = _ctl_cfg(policy_override="p2c")
    cfg.conversations[0].turns.append(ScenarioTurn(question="q2"))
    n = []

    async def handler(request):
        n.append(1)
        hdrs = (
            {"x-policy-override-applied": "p2c"}
            if len(n) == 1
            else {"x-place-decision": "w"}
        )
        return httpx.Response(200, headers=hdrs, json={"id": "x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        s = await ScenarioReplayer(
            cfg, "http://gw", client=client, gateway_stream=False
        ).run()
    assert [t.status for t in s.turn_results] == ["completed", "control_not_applied"]
    assert s.failed_turns == 1


@pytest.mark.anyio
async def test_connection_failure_before_headers_leaves_control_unobserved():
    cfg = _ctl_cfg(policy_override="p2c")

    async def handler(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rp = ScenarioReplayer(cfg, "http://gw", client=client, gateway_stream=False)
        s = await rp.run()
    assert s.failed_turns == 1
    assert rp.control_observations == {"policy_override": [0, 0]}  # never verified


@pytest.mark.anyio
async def test_gateway_sends_scenario_max_tokens():
    seen = []

    async def handler(request):
        seen.append(json.loads(request.content)["max_tokens"])
        return httpx.Response(200, json={"id": "x"})

    for cfg in (_gw_cfg(), _gw_cfg().model_copy(update={"max_tokens": 128})):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await ScenarioReplayer(
                cfg, "http://gw", client=client, gateway_stream=False
            ).run()
    assert seen == [512, 128]


@pytest.mark.anyio
async def test_replayer_arrival_rate_uniform_and_poisson():
    cfg = ScenarioConfig(
        name="test_rate_replay",
        description="Test arrival rate scheduling",
        concurrency=10,
        strategy="manual",
        target_endpoint_type="gateway_chat",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix=f"c{i}",
                turns=[ScenarioTurn(question=f"q{i}")],
            )
            for i in range(3)
        ],
    )

    dispatch_times: list[float] = []

    async def handler(request):
        import time

        dispatch_times.append(time.perf_counter())
        return httpx.Response(200, json={"id": "x"})

    # Test uniform arrival rate
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rp_uniform = ScenarioReplayer(
            cfg,
            "http://gw",
            client=client,
            gateway_stream=False,
            arrival_rate=100.0,
            arrival_distribution="uniform",
        )
        s_uniform = await rp_uniform.run()

    assert s_uniform.arrival_rate == 100.0
    assert s_uniform.arrival_distribution == "uniform"
    assert s_uniform.successful_turns == 3
    assert len(dispatch_times) == 3

    # Test poisson arrival rate
    dispatch_times.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rp_poisson = ScenarioReplayer(
            cfg,
            "http://gw",
            client=client,
            gateway_stream=False,
            arrival_rate=100.0,
            arrival_distribution="poisson",
        )
        s_poisson = await rp_poisson.run()

    assert s_poisson.arrival_rate == 100.0
    assert s_poisson.arrival_distribution == "poisson"
    assert s_poisson.successful_turns == 3
    assert len(dispatch_times) == 3


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

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            body = json.loads(request.content.decode("utf-8"))
            conv_id = body.get("conversation_id", "conv_default")
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
                    "conversation_id": "c1",
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

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                202,
                json={
                    "conversation_id": body.get("conversation_id"),
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

"""Unit tests for ServeLLMClient and vLLM / serve gateway integration."""

import json

import httpx
import pytest
from app.config import LLMConfigurationError, Settings
from app.llm import (
    LLMProviderError,
    LocalFakeLLMClient,
    ServeLLMClient,
    create_llm_client,
)


def test_settings_validate_inference_alignment() -> None:
    settings = Settings(
        llm_provider="vllm",
        inference_gateway_url="http://localhost:18080/serve",
        inference_model_id="Qwen/Qwen3-0.6B",
    )
    settings.validate_inference_alignment()

    with pytest.raises(LLMConfigurationError, match="must be a valid HTTP"):
        Settings(
            llm_provider="vllm",
            inference_gateway_url="invalid-url",
            inference_model_id="Qwen/Qwen3-0.6B",
        ).validate_inference_alignment()

    with pytest.raises(LLMConfigurationError, match="M4 requires LLM_PROVIDER=bedrock"):
        settings.validate_m4_alignment()


def test_create_llm_client_factory() -> None:
    fake_settings = Settings(llm_provider="fake")
    assert isinstance(create_llm_client(fake_settings), LocalFakeLLMClient)

    vllm_settings = Settings(
        llm_provider="vllm",
        inference_gateway_url="http://localhost:18080/serve",
        inference_model_id="Qwen/Qwen3-0.6B",
    )
    client = create_llm_client(vllm_settings)
    assert isinstance(client, ServeLLMClient)


def test_serve_llm_client_ask_headers_and_payload() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        data = json.loads(request.content)
        assert data["model"] == "Qwen/Qwen3-0.6B"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "system"
        assert data["messages"][1]["role"] == "user"

        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Manhattan has high taxi volume.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 10,
                    "total_tokens": 60,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=http_client,
    )

    result = serve_client.ask(
        "Tell me about Manhattan taxis.", conversation_id="conv-123"
    )
    assert result.text == "Manhattan has high taxi volume."
    assert result.input_tokens == 50
    assert result.output_tokens == 10

    # Verify headers
    req = captured_requests[0]
    headers = req.headers
    assert headers["x-conversation-id"] == "conv-123"
    assert headers["x-agent-step"] == "1"
    assert "x-request-id" in headers
    assert "x-prefix-id" in headers
    assert int(headers["x-estimated-prompt-tokens"]) > 0


def test_serve_llm_client_propose_taxi_query_tool_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert "tools" in data
        assert data["tool_choice"] == "auto"

        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "query_taxi_data",
                                        "arguments": json.dumps(
                                            {"analysis": "top_pickup_zones", "limit": 5}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 15},
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=http_client,
    )

    schema = {"columns": ["pickup_zone", "trip_count"]}
    proposal = serve_client.propose_taxi_query("What are the top pickup zones?", schema)
    assert proposal.name == "query_taxi_data"
    assert proposal.arguments == {"analysis": "top_pickup_zones", "limit": 5}
    assert proposal.input_tokens == 80


def test_serve_llm_client_stream_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["stream"] is True

        chunks = [
            'data: {"choices": [{"delta": {"content": "There were "}}]}\n\n',
            'data: {"choices": [{"delta": {"content": "1,000 trips."}}]}\n\n',
            "data: [DONE]\n\n",
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(chunks).encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=http_client,
    )

    deltas: list[str] = []
    result = serve_client.stream_answer_with_query_result(
        "Summarize trips",
        {"row_count": 1000},
        delta_callback=deltas.append,
    )
    assert "".join(deltas) == "There were 1,000 trips."
    assert result.text == "There were 1,000 trips."


def test_serve_llm_client_error_handling() -> None:
    # 1. 503 Service Unavailable -> retryable
    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Worker capacity unavailable")

    transport = httpx.MockTransport(handler_503)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )
    with pytest.raises(LLMProviderError) as exc_info:
        serve_client.ask("test")
    assert exc_info.value.retryable is True
    assert exc_info.value.code == "vllm_gateway_http_503"

    # 2. Connection failure -> retryable
    def handler_conn(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport_conn = httpx.MockTransport(handler_conn)
    serve_client_conn = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport_conn),
    )
    with pytest.raises(LLMProviderError) as exc_info:
        serve_client_conn.ask("test")
    assert exc_info.value.retryable is True
    assert exc_info.value.code == "vllm_gateway_unavailable"

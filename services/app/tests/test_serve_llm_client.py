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


def test_propose_taxi_query_400_raises_and_sends_exactly_one_request() -> None:
    """#115 slice B: a 400 must never trigger a silent retry with tools stripped."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"error": "tool calling not enabled"})

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        serve_client.propose_taxi_query(
            "What are the top pickup zones?", {"columns": ["pickup_zone"]}
        )
    assert exc_info.value.code == "vllm_gateway_http_400"
    assert exc_info.value.retryable is False
    assert call_count == 1


def test_propose_taxi_query_no_tool_calls_is_a_typed_failure_not_a_keyword_guess() -> (
    None
):
    """No tool_calls in the response must never fall back to keyword-matching the
    prompt or content; it must raise a typed, non-retryable failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I would call average_trip_metrics here.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        serve_client.propose_taxi_query(
            "What is the average fare by borough?", {"columns": ["pickup_zone"]}
        )
    assert exc_info.value.code == "no_tool_call"
    assert exc_info.value.retryable is False


def test_propose_taxi_query_malformed_tool_call_arguments_is_invalid_tool_call() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
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
                                        "arguments": "{not valid json",
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        serve_client.propose_taxi_query("top zones?", {"columns": ["pickup_zone"]})
    assert exc_info.value.code == "invalid_tool_call"
    assert exc_info.value.retryable is False


def test_propose_taxi_query_disables_thinking_and_records_finish_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
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
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 15},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )
    proposal = serve_client.propose_taxi_query(
        "top zones?", {"columns": ["pickup_zone"]}
    )
    assert proposal.finish_reason == "tool_calls"
    assert proposal.configured_max_tokens == 512


def test_ask_strips_think_block_from_non_streaming_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<think>the user wants a summary</think>"
                                "Manhattan has the most pickups."
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )
    result = serve_client.ask("Summarize", conversation_id="conv-1")
    assert result.text == "Manhattan has the most pickups."
    assert "<think>" not in result.text
    assert result.finish_reason == "stop"
    assert result.visible_answer_tokens is None
    assert (
        result.visible_answer_tokens_unavailable_reason
        == "provider_did_not_report_token_split"
    )


def test_stream_answer_never_delivers_think_tag_split_across_chunks() -> None:
    """A `<think>` tag split across SSE chunk boundaries must never leak into a
    delta the stream callback receives, and first_visible_answer_ms must be measured
    from the first VISIBLE delta, not the reasoning text."""

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            'data: {"choices": [{"delta": {"content": "<thi"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": "nk>reasoning here"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": "</thi"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": "nk>Manhattan wins."}}]}\n\n',
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n',
            "data: [DONE]\n\n",
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(chunks).encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )
    deltas: list[str] = []
    result = serve_client.stream_answer_with_query_result(
        "Summarize trips", {"row_count": 1000}, delta_callback=deltas.append
    )
    full_delivered = "".join(deltas)
    assert "<think>" not in full_delivered
    assert "</think>" not in full_delivered
    assert "reasoning here" not in full_delivered
    assert full_delivered == "Manhattan wins."
    assert result.text == "Manhattan wins."
    assert result.finish_reason == "stop"
    assert result.first_visible_answer_ms is not None
    assert result.ttft_ms is not None
    assert result.ttft_ms <= result.first_visible_answer_ms


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


def test_stream_answer_json_response_does_not_truncate_trailing_text() -> None:
    """#138 review major: when the gateway returns a plain JSON body (not SSE) for
    a streaming call, any text the ThinkingSplitter is still holding back (up to
    len(tag)-1 trailing characters) must be flushed, not silently dropped."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "The answer is 42 trips.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )
    deltas: list[str] = []
    result = serve_client.stream_answer_with_query_result(
        "Summarize trips", {"row_count": 1000}, delta_callback=deltas.append
    )
    full_delivered = "".join(deltas)
    assert full_delivered == "The answer is 42 trips."
    assert result.text == "The answer is 42 trips."
    assert result.finish_reason == "stop"
    # Non-streaming-shaped-as-stream: the full body was already read before any
    # delta was emitted, so there is no genuine time-to-first-token to report.
    assert result.ttft_ms is None
    assert result.ttft_unavailable_reason == "non_streaming_json_response"


def test_propose_taxi_query_no_tool_call_carries_telemetry_for_failed_call() -> None:
    """#138 review major: a no_tool_call failure must still carry the served
    model id, usage, latency, and finish_reason so callers can record a failed
    LLMCall instead of losing all telemetry on failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "no tool used"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        serve_client.propose_taxi_query("top zones?", {"columns": ["pickup_zone"]})
    err = exc_info.value
    assert err.code == "no_tool_call"
    assert err.model_id == "Qwen/Qwen3-0.6B"
    assert err.input_tokens == 40
    assert err.output_tokens == 8
    assert err.finish_reason == "stop"
    assert err.latency_ms is not None

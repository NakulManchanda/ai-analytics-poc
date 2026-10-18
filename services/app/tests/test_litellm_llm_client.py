"""LiteLLMClient contract tests: alias routing, request shaping, error mapping."""

import json

import httpx
import pytest
from app.config import LLMConfigurationError, Settings
from app.llm import LiteLLMClient, LLMProviderError, create_llm_client

PROXY = "http://litellm:4000"


def _client(handler, **kwargs) -> LiteLLMClient:
    return LiteLLMClient(
        PROXY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _completion(content: str = "hi", **extra) -> dict:
    message = {"role": "assistant", "content": content, **extra}
    return {
        "model": "taxi-answer",
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def test_settings_and_factory() -> None:
    settings = Settings(llm_provider="litellm")
    assert isinstance(create_llm_client(settings), LiteLLMClient)
    with pytest.raises(LLMConfigurationError):
        Settings(
            llm_provider="litellm", litellm_proxy_url="nope"
        ).validate_litellm_alignment()


def test_ask_uses_answer_alias_and_strips_vllm_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion("answer"))

    result = _client(handler, api_key="sk-test").ask("hello")
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == f"{PROXY}/v1/chat/completions"
    assert body["model"] == "taxi-answer"
    assert "chat_template_kwargs" not in body
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    assert result.text == "answer"
    assert (result.input_tokens, result.output_tokens) == (7, 3)


def test_tool_proposal_uses_planner_alias() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        call = {
            "function": {
                "name": "query_taxi_data",
                "arguments": '{"analysis": "top_pickup_zones", "limit": 5}',
            }
        }
        return httpx.Response(200, json=_completion("", tool_calls=[call]))

    proposal = _client(handler).propose_taxi_query("top zones?", {"columns": []})
    assert seen[0]["model"] == "taxi-planner"
    assert seen[0]["tools"]
    assert proposal.name == "query_taxi_data"
    assert proposal.arguments == {"analysis": "top_pickup_zones", "limit": 5}


def test_streaming_deltas_ttft_and_usage() -> None:
    seen: list[dict] = []
    sse = (
        'data: {"model":"taxi-answer","choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse
        )

    deltas: list[str] = []
    result = _client(handler).stream_answer_with_query_result(
        "q", {"rows": []}, deltas.append
    )
    assert seen[0]["stream_options"] == {"include_usage": True}
    assert "".join(deltas) == "Hello"
    assert result.text == "Hello"
    assert result.ttft_ms is not None
    assert (result.input_tokens, result.output_tokens) == (5, 2)
    assert result.finish_reason == "stop"


@pytest.mark.parametrize(
    ("status", "retryable"), [(503, True), (504, True), (429, False), (400, False)]
)
def test_http_errors_map_deterministically(status: int, retryable: bool) -> None:
    client = _client(lambda r: httpx.Response(status))
    with pytest.raises(LLMProviderError) as exc:
        client.ask("x")
    assert exc.value.code == f"litellm_proxy_http_{status}"
    assert exc.value.retryable is retryable


def test_connect_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(LLMProviderError) as exc:
        _client(handler).ask("x")
    assert exc.value.code == "litellm_proxy_unavailable"
    assert exc.value.retryable is True


def test_conversation_id_becomes_litellm_session_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion("ok"))

    _client(handler).ask("hello", conversation_id="conv_abc")
    assert seen[0].headers["x-litellm-session-id"] == "conv_abc"
    assert seen[0].headers["x-conversation-id"] == "conv_abc"

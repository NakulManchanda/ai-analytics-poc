"""Contract and unit tests for the thin inference gateway service."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from infra.inference.gateway.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_gateway_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "inference-gateway"}


def test_gateway_serve_forwarding(client: TestClient) -> None:
    mock_upstream_response = httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": "Qwen/Qwen3-0.6B",
            "choices": [{"message": {"role": "assistant", "content": "Gateway response."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_upstream_response

        response = client.post(
            "/serve",
            json={
                "model": "Qwen/Qwen3-0.6B",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
            headers={
                "x-request-id": "req-gw-1",
                "x-conversation-id": "conv-gw-1",
                "x-agent-step": "1",
                "x-prefix-id": "prefix-hash-1234",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "Gateway response."

        # Verify correlation headers attached
        headers = response.headers
        assert headers["x-request-id"] == "req-gw-1"
        assert headers["x-conversation-id"] == "conv-gw-1"
        assert headers["x-agent-step"] == "1"
        assert headers["x-prefix-id"] == "prefix-hash-1234"
        assert headers["x-orchestration-stage"] == "worker_passthrough"
        assert headers["x-guard-decision"] == "allow"
        assert headers["x-admit-decision"] == "accept"
        assert headers["x-place-decision"] == "worker_a"


def test_gateway_serve_worker_unavailable(client: TestClient) -> None:
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused")

        response = client.post(
            "/serve",
            json={"model": "Qwen/Qwen3-0.6B", "messages": []},
            headers={"x-request-id": "req-fail"},
        )
        assert response.status_code == 503
        assert "Worker unavailable" in response.json()["detail"]
        assert response.headers["x-request-id"] == "req-fail"


def test_gateway_tokenize_forwarding(client: TestClient) -> None:
    """D17: the gateway proxies /tokenize to the worker so the app can get exact
    token counts without a local tokenizer dependency."""
    mock_upstream_response = httpx.Response(
        200,
        json={"count": 4, "tokens": [1, 2, 3, 4], "max_model_len": 4096},
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_upstream_response

        response = client.post(
            "/tokenize",
            json={"model": "Qwen/Qwen3-0.6B", "prompt": "Hello there"},
        )

    assert response.status_code == 200
    assert response.json() == {"count": 4, "tokens": [1, 2, 3, 4], "max_model_len": 4096}
    called_url = mock_post.call_args.args[0]
    assert called_url.endswith("/tokenize")


def test_gateway_tokenize_worker_unavailable(client: TestClient) -> None:
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("refused")

        response = client.post(
            "/tokenize",
            json={"model": "Qwen/Qwen3-0.6B", "prompt": "Hello there"},
        )

    assert response.status_code == 503

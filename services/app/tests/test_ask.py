import pytest
from app.config import Settings
from app.llm import (
    BEDROCK_RUNTIME_CONFIG,
    BedrockLLMClient,
    LLMResult,
    create_llm_client,
)
from app.main import create_app
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient


class FakeLLMClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            text="A short answer.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=4,
            output_tokens=3,
            latency_ms=17,
        )


def test_ask_returns_the_fake_client_answer_and_usage_metadata() -> None:
    llm_client = FakeLLMClient()
    client = TestClient(
        create_app(llm_client=llm_client, llm_call_id_factory=lambda: "llm_call_test_1")
    )

    response = client.post("/api/ask", json={"prompt": "Summarize this."})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "A short answer.",
        "llm_call_id": "llm_call_test_1",
        "model_id": "amazon.nova-micro-v1:0",
        "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
        "latency_ms": 17,
    }
    assert llm_client.prompts == ["Summarize this."]


def test_ask_assigns_a_distinct_opaque_id_to_each_call() -> None:
    llm_client = FakeLLMClient()
    call_ids = iter(["llm_call_test_1", "llm_call_test_2"])
    client = TestClient(
        create_app(llm_client=llm_client, llm_call_id_factory=lambda: next(call_ids))
    )

    first_response = client.post("/api/ask", json={"prompt": "First."})
    second_response = client.post("/api/ask", json={"prompt": "Second."})

    assert first_response.json()["llm_call_id"] == "llm_call_test_1"
    assert second_response.json()["llm_call_id"] == "llm_call_test_2"


def test_ask_rejects_a_whitespace_only_prompt_without_calling_the_client() -> None:
    llm_client = FakeLLMClient()
    client = TestClient(create_app(llm_client=llm_client))

    response = client.post("/api/ask", json={"prompt": "   "})

    assert response.status_code == 422
    assert llm_client.prompts == []


class FakeBedrockRuntimeClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def converse(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        return {
            "output": {"message": {"content": [{"text": "Bedrock answer."}]}},
            "usage": {"inputTokens": 6, "outputTokens": 4},
            "metrics": {"latencyMs": 23},
        }


def test_bedrock_client_maps_converse_response_and_uses_bounded_request() -> None:
    runtime_client = FakeBedrockRuntimeClient()
    llm_client = BedrockLLMClient(
        "amazon.nova-micro-v1:0", runtime_client=runtime_client
    )

    result = llm_client.ask("Give a concise answer.")

    assert result == LLMResult(
        text="Bedrock answer.",
        model_id="amazon.nova-micro-v1:0",
        input_tokens=6,
        output_tokens=4,
        latency_ms=23,
    )
    assert runtime_client.requests == [
        {
            "modelId": "amazon.nova-micro-v1:0",
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": "Give a concise answer."}],
                }
            ],
            "inferenceConfig": {"maxTokens": 128, "temperature": 0.0},
        }
    ]


class FailingBedrockRuntimeClient:
    def __init__(self, error_code: str, error_message: str) -> None:
        self._error_code = error_code
        self._error_message = error_message

    def converse(self, **_request: object) -> None:
        raise ClientError(
            {"Error": {"Code": self._error_code, "Message": self._error_message}},
            "Converse",
        )


@pytest.mark.parametrize(
    ("error_code", "status_code", "retryable"),
    [
        ("ThrottlingException", 503, True),
        ("AccessDeniedException", 502, False),
    ],
)
def test_ask_returns_a_controlled_provider_error_without_provider_detail(
    error_code: str, status_code: int, retryable: bool
) -> None:
    client = TestClient(
        create_app(
            llm_client=BedrockLLMClient(
                "amazon.nova-micro-v1:0",
                runtime_client=FailingBedrockRuntimeClient(
                    error_code, "internal provider detail"
                ),
            ),
            llm_call_id_factory=lambda: "llm_call_failure",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Summarize this."})

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {
            "code": "llm_provider_error",
            "llm_call_id": "llm_call_failure",
            "retryable": retryable,
        }
    }
    assert "internal provider detail" not in response.text


def test_ask_returns_a_controlled_nonretryable_configuration_error() -> None:
    client = TestClient(
        create_app(
            settings=Settings(aws_region="us-west-2"),
            llm_call_id_factory=lambda: "llm_call_config_failure",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Summarize this."})

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "llm_configuration_error",
            "llm_call_id": "llm_call_config_failure",
            "retryable": False,
        }
    }


@pytest.mark.parametrize(
    "settings",
    [
        Settings(aws_region="us-west-2"),
        Settings(llm_model_id="amazon.nova-lite-v1:0"),
    ],
)
def test_create_llm_client_rejects_settings_outside_the_m4_iam_allowlist(
    settings: Settings,
) -> None:
    with pytest.raises(ValueError):
        create_llm_client(settings)


def test_create_llm_client_disables_bedrock_runtime_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    runtime_client = object()

    def fake_boto3_client(
        service_name: str, *, region_name: str | None = None, config: Config | None = None
    ) -> object:
        captured["service_name"] = service_name
        captured["region_name"] = region_name
        captured["config"] = config
        return runtime_client

    import boto3

    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    llm_client = create_llm_client(Settings())

    assert llm_client._get_runtime_client() is runtime_client
    assert captured == {
        "service_name": "bedrock-runtime",
        "region_name": "us-east-1",
        "config": BEDROCK_RUNTIME_CONFIG,
    }

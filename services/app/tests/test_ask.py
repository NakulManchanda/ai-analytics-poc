import pytest
from app.config import Settings
from app.llm import (
    BEDROCK_RUNTIME_CONFIG,
    BedrockLLMClient,
    LLMResult,
    ToolProposalResult,
    create_llm_client,
)
from app.main import create_app
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
from fastapi.testclient import TestClient


class FakeLLMClient:
    def __init__(self) -> None:
        self.proposal_prompts: list[str] = []
        self.answer_prompts: list[str] = []

    def ask(self, prompt: str) -> LLMResult:
        self.answer_prompts.append(prompt)
        return LLMResult(
            text="A short answer.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=4,
            output_tokens=3,
            latency_ms=17,
        )

    def propose_dataset_profile(self, prompt: str) -> ToolProposalResult:
        self.proposal_prompts.append(prompt)
        return ToolProposalResult(
            name="get_dataset_profile",
            arguments={},
            model_id="amazon.nova-micro-v1:0",
            input_tokens=2,
            output_tokens=1,
            latency_ms=3,
        )

    def answer_with_dataset_profile(
        self, prompt: str, _dataset_profile: dict[str, object]
    ) -> LLMResult:
        return self.ask(prompt)

    def propose_taxi_query(
        self, prompt: str, _schema: dict[str, object]
    ) -> ToolProposalResult:
        self.proposal_prompts.append(prompt)
        return ToolProposalResult(
            name="query_taxi_data",
            arguments={"analysis": "top_pickup_zones", "limit": 5},
            model_id="amazon.nova-micro-v1:0",
            input_tokens=2,
            output_tokens=1,
            latency_ms=3,
        )

    def answer_with_query_result(
        self, prompt: str, _query_result: dict[str, object]
    ) -> LLMResult:
        return self.ask(prompt)


class FakeMCPClient:
    def get_dataset_profile(self) -> dict[str, object]:
        return {
            "row_count": 3,
            "zone_row_count": 2,
            "schema_columns": [],
            "daily_zone_rows": [],
            "duckdb_settings": {"threads": "1", "memory_limit": "512MB"},
            "timing_ms": 1,
            "rss_bytes": 1024,
        }

    def get_dataset_schema(self) -> dict[str, object]:
        return {
            "dataset": "nyc-yellow-taxi",
            "month": "2024-01",
            "columns": ["tpep_pickup_datetime", "PULocationID"],
        }

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]:
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["Alpha", 3]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "query_test_1",
            "truncated": False,
        }


def test_ask_returns_the_fake_client_answer_and_usage_metadata() -> None:
    llm_client = FakeLLMClient()
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=FakeMCPClient(),
            llm_call_id_factory=lambda: "llm_call_test_1",
            tool_call_id_factory=lambda: "tool_call_test_1",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Summarize this."})

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"].startswith("conv_")
    assert payload["run_id"].startswith("run_")
    del payload["conversation_id"]
    del payload["run_id"]
    assert payload == {
        "answer": "A short answer.",
        "tool_call_id": "tool_call_test_1",
        "query_id": "query_test_1",
        "llm_calls": [
            {
                "llm_call_id": "llm_call_test_1",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                "latency_ms": 3,
            },
            {
                "llm_call_id": "llm_call_test_1",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
                "latency_ms": 17,
            },
        ],
        "usage": {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
        "latency_ms": 20,
    }
    assert llm_client.proposal_prompts == ["Summarize this."]
    assert llm_client.answer_prompts == ["Summarize this."]


def test_ask_assigns_a_distinct_opaque_id_to_each_call() -> None:
    llm_client = FakeLLMClient()
    call_ids = iter(
        [
            "llm_call_test_1_proposal",
            "llm_call_test_1_answer",
            "llm_call_test_2_proposal",
            "llm_call_test_2_answer",
        ]
    )
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=FakeMCPClient(),
            llm_call_id_factory=lambda: next(call_ids),
        )
    )

    first_response = client.post("/api/ask", json={"prompt": "First."})
    second_response = client.post("/api/ask", json={"prompt": "Second."})

    assert [call["llm_call_id"] for call in first_response.json()["llm_calls"]] == [
        "llm_call_test_1_proposal",
        "llm_call_test_1_answer",
    ]
    assert [call["llm_call_id"] for call in second_response.json()["llm_calls"]] == [
        "llm_call_test_2_proposal",
        "llm_call_test_2_answer",
    ]


def test_ask_rejects_a_whitespace_only_prompt_without_calling_the_client() -> None:
    llm_client = FakeLLMClient()
    client = TestClient(create_app(llm_client=llm_client))

    response = client.post("/api/ask", json={"prompt": "   "})

    assert response.status_code == 422
    assert llm_client.proposal_prompts == []
    assert llm_client.answer_prompts == []


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


def test_bedrock_client_exposes_only_the_fixed_no_argument_profile_tool() -> None:
    class ToolUseRuntimeClient:
        def __init__(self) -> None:
            self.request: dict[str, object] | None = None

        def converse(self, **request: object) -> dict[str, object]:
            self.request = request
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "name": "get_dataset_profile",
                                    "input": {},
                                }
                            }
                        ]
                    }
                },
                "usage": {"inputTokens": 6, "outputTokens": 1},
                "metrics": {"latencyMs": 23},
            }

    runtime_client = ToolUseRuntimeClient()
    proposal = BedrockLLMClient(
        "amazon.nova-micro-v1:0", runtime_client=runtime_client
    ).propose_dataset_profile("What dataset is available?")

    assert proposal.name == "get_dataset_profile"
    assert proposal.arguments == {}
    assert runtime_client.request is not None
    tool_config = runtime_client.request["toolConfig"]
    assert tool_config == {
        "tools": [
            {
                "toolSpec": {
                    "name": "get_dataset_profile",
                    "description": "Return the fixed profile for the pinned NYC Taxi dataset.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        }
                    },
                }
            }
        ],
        "toolChoice": {"tool": {"name": "get_dataset_profile"}},
    }


class FailingBedrockRuntimeClient:
    def __init__(self, error_code: str, error_message: str) -> None:
        self._error_code = error_code
        self._error_message = error_message

    def converse(self, **_request: object) -> None:
        raise ClientError(
            {"Error": {"Code": self._error_code, "Message": self._error_message}},
            "Converse",
        )


class TimeoutBedrockRuntimeClient:
    def __init__(self, error: ConnectTimeoutError | ReadTimeoutError) -> None:
        self._error = error

    def converse(self, **_request: object) -> None:
        raise self._error


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
            mcp_client=FakeMCPClient(),
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


@pytest.mark.parametrize(
    "error",
    [
        ConnectTimeoutError(endpoint_url="https://bedrock-runtime.example.com"),
        ReadTimeoutError(endpoint_url="https://bedrock-runtime.example.com"),
    ],
)
def test_ask_marks_bedrock_transport_timeouts_retryable(
    error: ConnectTimeoutError | ReadTimeoutError,
) -> None:
    client = TestClient(
        create_app(
            llm_client=BedrockLLMClient(
                "amazon.nova-micro-v1:0",
                runtime_client=TimeoutBedrockRuntimeClient(error),
            ),
            mcp_client=FakeMCPClient(),
            llm_call_id_factory=lambda: "llm_call_transport_failure",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Summarize this."})

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "llm_provider_error",
            "llm_call_id": "llm_call_transport_failure",
            "retryable": True,
        }
    }


def test_ask_returns_a_controlled_nonretryable_configuration_error() -> None:
    client = TestClient(
        create_app(
            settings=Settings(aws_region="us-west-2"),
            mcp_client=FakeMCPClient(),
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
        service_name: str,
        *,
        region_name: str | None = None,
        config: Config | None = None,
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

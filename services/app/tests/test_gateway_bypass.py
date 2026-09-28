"""Tests detecting accidental gateway bypass, verifying MCP isolation,
and confirming end-to-end taxi agent execution on the serve path.
"""


import json
from typing import Any

import httpx
import pytest
from app.config import LLMConfigurationError, Settings
from app.llm import (
    BedrockLLMClient,
    LLMProviderError,
    ServeLLMClient,
    create_llm_client,
)
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository


class TrackingMCPClient:
    """Mock MCP client that tracks all calls and verifies none hit the inference gateway."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_dataset_schema(self) -> dict[str, Any]:
        self.calls.append("get_dataset_schema")
        return {
            "dataset": "nyc-taxi",
            "month": "2024-01",
            "columns": ["pickup_zone", "trip_distance", "fare_amount", "trip_count"],
        }

    def query_taxi_data(self, analysis: str, limit: int = 5) -> dict[str, Any]:
        self.calls.append(f"query_taxi_data:{analysis}:{limit}")
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["Midtown Center", 2500], ["Times Square", 2100]],
            "row_count": 2,
            "execution_duration_ms": 15,
            "query_id": "query-mcp-42",
            "truncated": False,
        }

    def average_trip_metrics(self, region_name: str | None = None) -> dict[str, Any]:
        self.calls.append(f"average_trip_metrics:{region_name}")
        return {
            "columns": [
                "region_name",
                "trip_count",
                "average_trip_distance",
                "average_fare_amount",
            ],
            "rows": [["Manhattan", 100000, 2.4, 18.5]],
            "row_count": 1,
            "execution_duration_ms": 20,
            "query_id": "query-mcp-43",
            "truncated": False,
        }


def test_taxi_agent_end_to_end_through_serve_path() -> None:
    gateway_requests: list[dict[str, Any]] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        headers = dict(request.headers)
        gateway_requests.append({"body": body, "headers": headers})

        # Step 1: Proposal
        if "tools" in body:
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
                    "usage": {"prompt_tokens": 120, "completion_tokens": 20},
                },
            )
        # Step 2: Final Answer
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "Midtown Center has the highest pickup volume with 2,500 trips."
                            ),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 160, "completion_tokens": 25},
            },
        )

    transport = httpx.MockTransport(gateway_handler)
    http_client = httpx.Client(transport=transport)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=http_client,
    )

    mcp = TrackingMCPClient()
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=serve_client,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
    )

    result = loop.run("What are the top pickup zones in NYC?")
    assert result.status == "completed"
    assert "Midtown Center has the highest pickup volume" in result.answer
    assert result.query_id == "query-mcp-42"

    # Verify every model step traversed the gateway
    assert len(gateway_requests) == 2

    # Step 1: Proposal verification
    step1 = gateway_requests[0]
    assert step1["headers"]["x-agent-step"] == "1"
    assert "x-request-id" in step1["headers"]
    assert "x-prefix-id" in step1["headers"]
    assert int(step1["headers"]["x-estimated-prompt-tokens"]) > 0

    # Step 2: Answer verification
    step2 = gateway_requests[1]
    assert step2["headers"]["x-agent-step"] == "2"
    assert step2["headers"]["x-prefix-id"] != ""

    # Verify MCP tool execution was independent and never routed through the gateway
    assert mcp.calls == ["get_dataset_schema", "query_taxi_data:top_pickup_zones:5"]
    for req in gateway_requests:
        assert "/mcp" not in str(req["body"])


def test_anti_bypass_guard_detects_accidental_bedrock_use_in_course_config() -> None:
    """Verifies that when configured for vLLM, Bedrock cannot be invoked silently."""
    settings = Settings(
        llm_provider="vllm",
        inference_gateway_url="http://localhost:18080/serve",
        inference_model_id="Qwen/Qwen3-0.6B",
    )

    # Calling M4 Bedrock alignment fails closed
    with pytest.raises(LLMConfigurationError, match="M4 requires LLM_PROVIDER=bedrock"):
        settings.validate_m4_alignment()

    # Factory creates ServeLLMClient, NOT Bedrock
    client = create_llm_client(settings)
    assert isinstance(client, ServeLLMClient)
    assert not isinstance(client, BedrockLLMClient)


def test_bedrock_remains_configurable_fallback_without_auto_failover() -> None:
    """Confirms Bedrock remains explicitly selectable through configuration."""
    bedrock_settings = Settings(
        llm_provider="bedrock",
        aws_region="us-east-1",
        llm_model_id="amazon.nova-micro-v1:0",
        dynamodb_table_name="TestTable",
    )
    bedrock_settings.validate_m4_alignment()

    # When in vLLM mode and cluster is down, fails closed rather than auto-falling back to Bedrock
    def handler_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Cluster connection refused")

    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=httpx.MockTransport(handler_fail)),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        serve_client.ask("test prompt")
    assert exc_info.value.code == "vllm_gateway_unavailable"
    assert exc_info.value.retryable is True

from __future__ import annotations

import pytest
from app.llm import LLMResult, ToolProposalResult
from app.main import create_app
from fastapi.testclient import TestClient

SCHEMA = {
    "dataset": "nyc-yellow-taxi",
    "month": "2024-01",
    "columns": ["tpep_pickup_datetime", "PULocationID", "trip_distance"],
}
QUERY_RESULT = {
    "columns": ["pickup_zone", "trip_count"],
    "rows": [["Alpha", 3], ["Beta", 2]],
    "row_count": 2,
    "execution_duration_ms": 7,
    "query_id": "query_m6_fixture",
    "truncated": False,
}
AVERAGE_METRICS_RESULT = {
    "columns": [
        "region_name",
        "trip_count",
        "average_trip_distance",
        "average_fare_amount",
    ],
    "rows": [["Manhattan", 3, 5.33, 18.0], ["Bronx", 2, 7.0, 18.0]],
    "row_count": 2,
    "execution_duration_ms": 7,
    "query_id": "query_average_metrics_fixture",
    "truncated": False,
}


class FakeGovernedQueryLLM:
    def __init__(self, proposal: dict[str, object] | None = None) -> None:
        self.proposal = proposal or {
            "name": "query_taxi_data",
            "arguments": {"analysis": "top_pickup_zones", "limit": 5},
        }
        self.proposal_inputs: list[tuple[str, dict[str, object]]] = []
        self.answer_inputs: list[tuple[str, dict[str, object]]] = []

    def propose_taxi_query(
        self, prompt: str, schema: dict[str, object]
    ) -> ToolProposalResult:
        self.proposal_inputs.append((prompt, schema))
        return ToolProposalResult(
            name=str(self.proposal["name"]),
            arguments=self.proposal["arguments"],
            model_id="amazon.nova-micro-v1:0",
            input_tokens=9,
            output_tokens=4,
            latency_ms=11,
        )

    def answer_with_query_result(
        self, prompt: str, query_result: dict[str, object]
    ) -> LLMResult:
        self.answer_inputs.append((prompt, query_result))
        return LLMResult(
            text="Alpha has the most pickups with 3 trips.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=15,
            output_tokens=8,
            latency_ms=17,
        )


class FakeGovernedQueryMCP:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result or QUERY_RESULT
        self.query_requests: list[tuple[str, int]] = []
        self.schema_reads = 0

    def get_dataset_schema(self) -> dict[str, object]:
        self.schema_reads += 1
        return SCHEMA

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]:
        self.query_requests.append((analysis, limit))
        return self.result


class FakeAverageTripMetricsLLM(FakeGovernedQueryLLM):
    def __init__(self) -> None:
        super().__init__(
            {
                "name": "average_trip_metrics",
                "arguments": {},
            }
        )

    def answer_with_query_result(
        self, prompt: str, query_result: dict[str, object]
    ) -> LLMResult:
        self.answer_inputs.append((prompt, query_result))
        return LLMResult(
            text="Manhattan averages 5.33 miles and $18.00 in fare across 3 trips.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=15,
            output_tokens=8,
            latency_ms=17,
        )


class FakeAverageTripMetricsMCP(FakeGovernedQueryMCP):
    def __init__(self) -> None:
        super().__init__()
        self.average_requests: list[str | None] = []

    def average_trip_metrics(
        self, *, region_name: str | None = None
    ) -> dict[str, object]:
        self.average_requests.append(region_name)
        return AVERAGE_METRICS_RESULT


def test_exact_borough_comparison_prompt_runs_the_governed_average_metrics_tool() -> (
    None
):
    """Catches routing the public borough comparison prompt to the old query tool."""
    prompt = (
        "Compare average trip distance and fare amount across major pickup boroughs"
    )
    llm_client = FakeAverageTripMetricsLLM()
    mcp_client = FakeAverageTripMetricsMCP()
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=mcp_client,
            llm_call_id_factory=iter(
                ["llm_average_proposal", "llm_average_answer"]
            ).__next__,
            tool_call_id_factory=lambda: "tool_average_metrics",
        )
    )

    response = client.post("/api/ask", json={"prompt": prompt})

    assert response.status_code == 200
    assert response.json()["answer"] == (
        "Manhattan averages 5.33 miles and $18.00 in fare across 3 trips."
    )
    assert mcp_client.average_requests == [None]
    assert mcp_client.query_requests == []
    assert llm_client.answer_inputs == [(prompt, AVERAGE_METRICS_RESULT)]


def test_ask_supplies_schema_and_runs_one_validated_governed_query() -> None:
    """Catches skipping schema context or issuing a different/unbounded MCP request."""
    llm_client = FakeGovernedQueryLLM()
    mcp_client = FakeGovernedQueryMCP()
    call_ids = iter(["llm_m6_proposal", "llm_m6_answer"])
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=mcp_client,
            llm_call_id_factory=lambda: next(call_ids),
            tool_call_id_factory=lambda: "tool_m6_query",
        )
    )

    response = client.post(
        "/api/ask", json={"prompt": "Which pickup zones have the most trips?"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"].startswith("conv_")
    assert payload["run_id"].startswith("run_")
    del payload["conversation_id"]
    del payload["run_id"]
    assert payload == {
        "answer": "Alpha has the most pickups with 3 trips.",
        "tool_call_id": "tool_m6_query",
        "query_id": "query_m6_fixture",
        "llm_calls": [
            {
                "llm_call_id": "llm_m6_proposal",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13},
                "latency_ms": 11,
            },
            {
                "llm_call_id": "llm_m6_answer",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {
                    "input_tokens": 15,
                    "output_tokens": 8,
                    "total_tokens": 23,
                },
                "latency_ms": 17,
            },
        ],
        "usage": {"input_tokens": 24, "output_tokens": 12, "total_tokens": 36},
        "latency_ms": 28,
    }
    assert llm_client.proposal_inputs == [
        ("Which pickup zones have the most trips?", SCHEMA)
    ]
    assert mcp_client.schema_reads == 1
    assert mcp_client.query_requests == [("top_pickup_zones", 5)]
    assert llm_client.answer_inputs == [
        ("Which pickup zones have the most trips?", QUERY_RESULT)
    ]


@pytest.mark.parametrize(
    "proposal",
    [
        {"name": "get_dataset_profile", "arguments": {}},
        {"name": "query_taxi_data", "arguments": {"sql": "select 1"}},
        {
            "name": "query_taxi_data",
            "arguments": {
                "analysis": "top_pickup_zones",
                "limit": 5,
                "path": "/tmp/data",
            },
        },
        {
            "name": "query_taxi_data",
            "arguments": {"analysis": "read_parquet", "limit": 5},
        },
        {
            "name": "query_taxi_data",
            "arguments": {"analysis": "top_pickup_zones", "limit": 21},
        },
    ],
)
def test_ask_rejects_nonexact_query_proposals_before_tool_execution(
    proposal: dict[str, object],
) -> None:
    """Catches SQL, paths, extra keys, unknown analyses, or oversized limits crossing MCP."""
    llm_client = FakeGovernedQueryLLM(proposal)
    mcp_client = FakeGovernedQueryMCP()
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=mcp_client,
            llm_call_id_factory=lambda: "llm_m6_rejected",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Run an unsafe query."})

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "tool_validation_error",
            "llm_call_id": "llm_m6_rejected",
            "retryable": False,
        }
    }
    assert mcp_client.query_requests == []
    assert llm_client.answer_inputs == []


def test_ask_rejects_malformed_query_envelope_before_final_model_call() -> None:
    """Catches trusting a missing/empty server-generated query identifier."""
    malformed_result = dict(QUERY_RESULT, query_id="")
    llm_client = FakeGovernedQueryLLM()
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=FakeGovernedQueryMCP(malformed_result),
            llm_call_id_factory=lambda: "llm_m6_result_error",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Which zones lead?"})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "mcp_tool_error"
    assert llm_client.answer_inputs == []

import pytest
from app.config import Settings
from app.llm import LLMResult, ToolProposalResult, create_llm_client
from app.main import create_app
from fastapi.testclient import TestClient


class FakeOneTurnLLM:
    def __init__(self, proposal: dict[str, object] | None = None) -> None:
        self.proposal_prompts: list[str] = []
        self.answer_inputs: list[tuple[str, dict[str, object]]] = []
        self.proposal = proposal or {
            "name": "query_taxi_data",
            "arguments": {"analysis": "top_pickup_zones", "limit": 5},
        }

    def propose_taxi_query(
        self, prompt: str, _schema: dict[str, object]
    ) -> ToolProposalResult:
        self.proposal_prompts.append(prompt)
        return ToolProposalResult(
            name=str(self.proposal["name"]),
            arguments=self.proposal["arguments"],
            model_id="amazon.nova-micro-v1:0",
            input_tokens=5,
            output_tokens=2,
            latency_ms=13,
        )

    def answer_with_query_result(
        self, prompt: str, query_result: dict[str, object]
    ) -> LLMResult:
        self.answer_inputs.append((prompt, query_result))
        return LLMResult(
            text="The profile contains 3 taxi trips.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=11,
            output_tokens=7,
            latency_ms=19,
        )


class FakeDatasetProfileMCP:
    def __init__(self) -> None:
        self.call_count = 0

    def get_dataset_schema(self) -> dict[str, object]:
        return {
            "dataset": "nyc-yellow-taxi",
            "month": "2024-01",
            "columns": ["tpep_pickup_datetime", "PULocationID"],
        }

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]:
        self.call_count += 1
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["Alpha", 3]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "query_m5_profile",
            "truncated": False,
        }


def test_ask_runs_one_validated_profile_tool_sequence_and_returns_bounded_metadata() -> (
    None
):
    llm_client = FakeOneTurnLLM()
    mcp_client = FakeDatasetProfileMCP()
    call_ids = iter(["llm_m5_proposal", "llm_m5_answer"])
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=mcp_client,
            llm_call_id_factory=lambda: next(call_ids),
            tool_call_id_factory=lambda: "tool_m5_profile",
        )
    )

    response = client.post("/api/ask", json={"prompt": "What dataset is available?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"].startswith("conv_")
    assert payload["run_id"].startswith("run_")
    del payload["conversation_id"]
    del payload["run_id"]
    assert payload == {
        "answer": "The profile contains 3 taxi trips.",
        "tool_call_id": "tool_m5_profile",
        "query_id": "query_m5_profile",
        "llm_calls": [
            {
                "llm_call_id": "llm_m5_proposal",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                "latency_ms": 13,
            },
            {
                "llm_call_id": "llm_m5_answer",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                "latency_ms": 19,
            },
        ],
        "usage": {"input_tokens": 16, "output_tokens": 9, "total_tokens": 25},
        "latency_ms": 32,
    }
    assert llm_client.proposal_prompts == ["What dataset is available?"]
    assert mcp_client.call_count == 1
    assert llm_client.answer_inputs == [
        (
            "What dataset is available?",
            {
                "columns": ["pickup_zone", "trip_count"],
                "rows": [["Alpha", 3]],
                "row_count": 1,
                "execution_duration_ms": 1,
                "query_id": "query_m5_profile",
                "truncated": False,
            },
        )
    ]


@pytest.mark.parametrize(
    "proposal",
    [
        {"name": "query_taxi_data", "arguments": {"sql": "select 1"}},
        {"name": "get_dataset_profile", "arguments": {"dataset": "other"}},
    ],
)
def test_ask_rejects_any_nonexact_profile_proposal_before_mcp(
    proposal: dict[str, object],
) -> None:
    llm_client = FakeOneTurnLLM(proposal=proposal)
    mcp_client = FakeDatasetProfileMCP()
    client = TestClient(
        create_app(
            llm_client=llm_client,
            mcp_client=mcp_client,
            llm_call_id_factory=lambda: "llm_m5_rejected",
        )
    )

    response = client.post("/api/ask", json={"prompt": "Run a query."})

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "tool_validation_error",
            "llm_call_id": "llm_m5_rejected",
            "retryable": False,
        }
    }
    assert mcp_client.call_count == 0
    assert llm_client.answer_inputs == []


def test_local_fake_llm_supports_the_same_fixed_one_turn_sequence_without_aws() -> None:
    llm_client = create_llm_client(Settings(llm_provider="fake"))

    proposal = llm_client.propose_taxi_query(
        "Which pickup zones have the most trips?",
        {
            "dataset": "nyc-yellow-taxi",
            "month": "2024-01",
            "columns": ["tpep_pickup_datetime", "PULocationID"],
        },
    )
    answer = llm_client.answer_with_query_result(
        "Which pickup zones have the most trips?",
        {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["Alpha", 3]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "query_fake",
            "truncated": False,
        },
    )

    assert proposal.name == "query_taxi_data"
    assert proposal.arguments == {"analysis": "top_pickup_zones", "limit": 5}
    assert answer.text == "Alpha has the most pickups with 3 trips."


def test_local_fake_llm_answers_the_public_borough_comparison_prompt() -> None:
    """Catches the local smoke path rejecting the four-column governed metrics result."""
    llm_client = create_llm_client(Settings(llm_provider="fake"))
    prompt = (
        "Compare average trip distance and fare amount across major pickup boroughs"
    )

    proposal = llm_client.propose_taxi_query(
        prompt,
        {
            "dataset": "nyc-yellow-taxi",
            "month": "2024-01",
            "columns": ["tpep_pickup_datetime", "PULocationID"],
        },
    )
    answer = llm_client.answer_with_query_result(
        prompt,
        {
            "columns": [
                "region_name",
                "trip_count",
                "average_trip_distance",
                "average_fare_amount",
            ],
            "rows": [["Manhattan", 3, 5.33, 18.0]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "query_average_metrics",
            "truncated": False,
        },
    )

    assert proposal.name == "average_trip_metrics"
    assert proposal.arguments == {}
    assert (
        answer.text
        == "Manhattan averages 5.33 miles and $18.00 in fare across 3 trips."
    )

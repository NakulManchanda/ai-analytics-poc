import pytest
from app.config import Settings
from app.llm import LLMResult, ToolProposalResult, create_llm_client
from app.main import create_app
from fastapi.testclient import TestClient


class FakeOneTurnLLM:
    def __init__(self, proposal: dict[str, object] | None = None) -> None:
        self.proposal_prompts: list[str] = []
        self.answer_inputs: list[tuple[str, dict[str, object]]] = []
        self.proposal = proposal or {"name": "get_dataset_profile", "arguments": {}}

    def propose_dataset_profile(self, prompt: str) -> ToolProposalResult:
        self.proposal_prompts.append(prompt)
        return ToolProposalResult(
            name=str(self.proposal["name"]),
            arguments=self.proposal["arguments"],
            model_id="amazon.nova-micro-v1:0",
            input_tokens=5,
            output_tokens=2,
            latency_ms=13,
        )

    def answer_with_dataset_profile(
        self, prompt: str, dataset_profile: dict[str, object]
    ) -> LLMResult:
        self.answer_inputs.append((prompt, dataset_profile))
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

    def get_dataset_profile(self) -> dict[str, object]:
        self.call_count += 1
        return {
            "row_count": 3,
            "zone_row_count": 2,
            "schema_columns": ["tpep_pickup_datetime", "PULocationID"],
            "daily_zone_rows": [],
            "duckdb_settings": {"threads": "1", "memory_limit": "512MB"},
            "timing_ms": 1,
            "rss_bytes": 1024,
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
    assert response.json() == {
        "answer": "The profile contains 3 taxi trips.",
        "tool_call_id": "tool_m5_profile",
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
                "row_count": 3,
                "zone_row_count": 2,
                "schema_columns": ["tpep_pickup_datetime", "PULocationID"],
                "daily_zone_rows": [],
                "duckdb_settings": {"threads": "1", "memory_limit": "512MB"},
                "timing_ms": 1,
                "rss_bytes": 1024,
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


def test_local_fake_llm_supports_the_same_fixed_m5_sequence_without_aws() -> None:
    llm_client = create_llm_client(Settings(llm_provider="fake"))

    proposal = llm_client.propose_dataset_profile("What dataset is available?")
    answer = llm_client.answer_with_dataset_profile(
        "What dataset is available?",
        {
            "row_count": 3,
            "zone_row_count": 2,
            "schema_columns": [],
            "daily_zone_rows": [],
            "duckdb_settings": {"threads": "1", "memory_limit": "512MB"},
            "timing_ms": 1,
            "rss_bytes": 1024,
        },
    )

    assert proposal.name == "get_dataset_profile"
    assert proposal.arguments == {}
    assert answer.text == "The profile contains 3 taxi trips."

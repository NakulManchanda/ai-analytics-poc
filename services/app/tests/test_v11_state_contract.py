from __future__ import annotations

from app.llm import LLMResult, ToolProposalResult
from app.main import create_app
from app.orchestration import LoopResult
from app.state import DynamoDBStateRepository, InMemoryStateRepository, StateError
from fastapi.testclient import TestClient


class ContractLLMClient:
    def propose_taxi_query(
        self, _prompt: str, _schema: dict[str, object]
    ) -> ToolProposalResult:
        return ToolProposalResult(
            name="query_taxi_data",
            arguments={"analysis": "top_pickup_zones", "limit": 5},
            model_id="amazon.nova-micro-v1:0",
            input_tokens=2,
            output_tokens=1,
            latency_ms=3,
        )

    def answer_with_query_result(
        self, _prompt: str, _query_result: dict[str, object]
    ) -> LLMResult:
        return LLMResult(
            text="JFK Airport has the most trips.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=4,
            output_tokens=3,
            latency_ms=17,
        )


class ContractMCPClient:
    def get_dataset_schema(self) -> dict[str, object]:
        return {"dataset": "nyc-taxi", "month": "2024-01", "columns": ["PULocationID"]}

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, object]:
        assert analysis == "top_pickup_zones"
        assert limit == 5
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["JFK Airport", 1500]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "qry_contract_1",
            "truncated": False,
        }


def test_create_app_uses_memory_state_when_no_dynamodb_table_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)

    application = create_app()

    assert isinstance(application.state.state_repository, InMemoryStateRepository)


def test_create_app_uses_dynamodb_state_when_table_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "configured-application-state")

    application = create_app()

    assert isinstance(application.state.state_repository, DynamoDBStateRepository)


def test_ask_delegates_to_the_application_orchestration_loop() -> None:
    class RecordingLoop:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def run(self, prompt: str, conversation_id: str | None = None) -> LoopResult:
            self.calls.append((prompt, conversation_id))
            return LoopResult(
                answer="Loop-owned answer.",
                status="completed",
                run_id="run_backend_generated",
                conversation_id="conv_backend_generated",
                tool_call_id="tcall_backend_generated",
                query_id="qry_backend_generated",
                input_tokens=2,
                output_tokens=1,
                total_tokens=3,
                latency_ms=5,
            )

    loop = RecordingLoop()
    client = TestClient(create_app(orchestration_loop=loop))

    response = client.post("/api/ask", json={"prompt": "Use the one loop."})

    assert response.status_code == 200
    assert response.json()["answer"] == "Loop-owned answer."
    assert response.json()["conversation_id"] == "conv_backend_generated"
    assert response.json()["run_id"] == "run_backend_generated"
    assert loop.calls == [("Use the one loop.", None)]


def test_two_ask_turns_persist_and_reload_the_durable_conversation() -> None:
    repo = InMemoryStateRepository()
    client = TestClient(
        create_app(
            llm_client=ContractLLMClient(),
            mcp_client=ContractMCPClient(),
            state_repository=repo,
        )
    )

    first = client.post("/api/ask", json={"prompt": "First question."})
    first_payload = first.json()
    second = client.post(
        "/api/ask",
        json={
            "prompt": "Second question.",
            "conversation_id": first_payload["conversation_id"],
        },
    )
    reloaded = client.get(f"/api/conversations/{first_payload['conversation_id']}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_payload["conversation_id"].startswith("conv_")
    assert first_payload["run_id"].startswith("run_")
    assert second.json()["conversation_id"] == first_payload["conversation_id"]
    assert second.json()["run_id"] != first_payload["run_id"]
    messages = repo.list_messages(first_payload["conversation_id"])
    assert [(message.role, message.content) for message in messages] == [
        ("user", "First question."),
        ("assistant", "JFK Airport has the most trips."),
        ("user", "Second question."),
        ("assistant", "JFK Airport has the most trips."),
    ]
    assert reloaded.status_code == 200
    assert [message["content"] for message in reloaded.json()["messages"]] == [
        "First question.",
        "JFK Airport has the most trips.",
        "Second question.",
        "JFK Airport has the most trips.",
    ]
    assert [run["run_id"] for run in reloaded.json()["runs"]] == [
        first_payload["run_id"],
        second.json()["run_id"],
    ]
    assert all(run["completed_at"] is not None for run in reloaded.json()["runs"])
    assert all(run["steps"] for run in reloaded.json()["runs"])


def test_ask_returns_a_server_error_when_durable_state_is_unavailable() -> None:
    class UnavailableStateRepository:
        def get_conversation(self, _conversation_id: str) -> None:
            raise StateError("DynamoDB is unavailable")

    client = TestClient(
        create_app(
            llm_client=ContractLLMClient(),
            mcp_client=ContractMCPClient(),
            state_repository=UnavailableStateRepository(),  # type: ignore[arg-type]
        ),
        raise_server_exceptions=False,
    )

    response = client.post("/api/ask", json={"prompt": "Persist this."})

    assert response.status_code == 500

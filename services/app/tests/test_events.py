from __future__ import annotations

from typing import Any

from app.events import (
    InMemoryEventPublisher,
    RunEvent,
)
from app.llm import LocalFakeLLMClient
from app.main import create_app
from app.orchestration import ExecutionBudgets, OrchestrationLoop
from app.state import (
    Conversation,
    InMemoryStateRepository,
    Run,
    RunStep,
    generate_conversation_id,
    generate_run_id,
    generate_step_id,
)
from fastapi.testclient import TestClient


class FakeMCPClient:
    def get_dataset_schema(self) -> dict[str, Any]:
        return {
            "dataset": "nyc-taxi",
            "month": "2024-01",
            "columns": ["PULocationID", "DOLocationID", "trip_distance", "fare_amount"],
        }

    def query_taxi_data(self, analysis: str, limit: int = 5) -> dict[str, Any]:
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["JFK Airport", 1500], ["LaGuardia Airport", 1200]],
            "row_count": 2,
            "execution_duration_ms": 12,
            "query_id": "fake-query-123",
            "truncated": False,
        }


def test_run_event_serialization_and_sse_formatting() -> None:
    event = RunEvent(
        event_type="tool.completed",
        run_id="run_123",
        conversation_id="conv_123",
        sequence=1,
        tool_call_id="tcall_123",
        query_id="qry_123",
        payload={"row_count": 5, "duration_ms": 25},
    )

    # To Dict
    data = event.to_dict()
    assert data["event_type"] == "tool.completed"
    assert data["run_id"] == "run_123"
    assert data["sequence"] == "1"

    # From Dict
    reconstructed = RunEvent.from_dict(data)
    assert reconstructed.event_id == event.event_id
    assert reconstructed.event_type == "tool.completed"
    assert reconstructed.payload == {"row_count": 5, "duration_ms": 25}

    # To SSE
    sse = event.to_sse()
    assert f"id: {event.event_id}" in sse
    assert "event: tool.completed" in sse
    assert '"tool_call_id": "tcall_123"' in sse


def test_events_endpoint_404_for_unknown_run() -> None:
    repo = InMemoryStateRepository()
    client = TestClient(create_app(state_repository=repo))

    response = client.get("/api/runs/non-existent-run/events")
    assert response.status_code == 404


def test_events_endpoint_reconstructs_from_durable_state() -> None:
    repo = InMemoryStateRepository()
    conv_id = generate_conversation_id()
    run_id = generate_run_id()

    repo.create_conversation(Conversation(conversation_id=conv_id))
    repo.create_run(
        Run(
            run_id=run_id,
            conversation_id=conv_id,
            status="completed",
            input_tokens=150,
            output_tokens=60,
            estimated_cost_usd=0.002,
        )
    )
    repo.add_run_step(
        RunStep(
            step_id=generate_step_id(),
            run_id=run_id,
            sequence=1,
            step_type="tool_call",
            status="completed",
            tool_name="query_taxi_data",
            query_id="qry_test_456",
            input_summary="analysis=top_pickup_zones",
            output_summary="rows=2",
        )
    )

    client = TestClient(create_app(state_repository=repo))
    response = client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    content = response.text
    assert "event: run.received" in content
    assert "event: step.tool_call" in content
    assert "event: run.completed" in content
    assert "qry_test_456" in content


def test_events_endpoint_reconstructs_durable_context_and_telemetry() -> None:
    repo = InMemoryStateRepository()
    conv_id = generate_conversation_id()
    run_id = generate_run_id()
    telemetry = {
        "end_to_end_latency_ms": 41,
        "proposal_llm_latency_ms": 11,
        "tool_latency_ms": 7,
        "final_answer_llm_latency_ms": 19,
        "ttft": {"available": False, "reason": "non_streaming_blocking"},
    }
    working_context = {
        "stored_message_count": 5,
        "included_message_count": 3,
        "conversation_summary": "user: first question",
    }
    repo.create_conversation(Conversation(conversation_id=conv_id))
    repo.create_run(
        Run(
            run_id=run_id,
            conversation_id=conv_id,
            status="completed",
            metadata={"telemetry": telemetry},
        )
    )
    repo.add_run_step(
        RunStep(
            step_id=generate_step_id(),
            run_id=run_id,
            sequence=1,
            step_type="context_reduced",
            status="completed",
            metadata={"working_context": working_context},
        )
    )

    response = TestClient(create_app(state_repository=repo)).get(
        f"/api/runs/{run_id}/events"
    )

    assert "event: context.reduced" in response.text
    assert '"stored_message_count": 5' in response.text
    assert '"end_to_end_latency_ms": 41' in response.text
    assert '"non_streaming_blocking"' in response.text


def test_orchestration_loop_emits_full_event_sequence() -> None:
    repo = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    llm = LocalFakeLLMClient()
    mcp = FakeMCPClient()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "completed"

    events = publisher.get_events_for_run(result.run_id)
    event_types = [e.event_type for e in events]
    expected_order = [
        "run.received",
        "context.loading",
        "llm.started",
        "llm.completed",
        "tool.requested",
        "tool.started",
        "tool.completed",
        "context.reduced",
        "llm.started",
        "llm.completed",
        "run.completed",
    ]
    assert event_types == expected_order


def test_orchestration_loop_emits_budget_exceeded_event() -> None:
    repo = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    llm = LocalFakeLLMClient()
    mcp = FakeMCPClient()

    budgets = ExecutionBudgets(max_iterations=0)
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
        event_publisher=publisher,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"

    events = publisher.get_events_for_run(result.run_id)
    event_types = [e.event_type for e in events]
    assert "run.received" in event_types
    assert "run.budget_exceeded" in event_types

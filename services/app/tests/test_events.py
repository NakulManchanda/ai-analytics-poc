from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from app.events import (
    InMemoryEventPublisher,
    RunEvent,
)
from app.llm import LLMProviderError, LLMResult, LocalFakeLLMClient, ToolProposalResult
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


def _sse_events(response_text: str) -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for frame in response_text.strip().split("\n\n"):
        event_type = next(
            (
                line.removeprefix("event: ")
                for line in frame.splitlines()
                if line.startswith("event: ")
            ),
            None,
        )
        data = next(
            (
                line.removeprefix("data: ")
                for line in frame.splitlines()
                if line.startswith("data: ")
            ),
            None,
        )
        if event_type is not None and data is not None:
            events[event_type] = json.loads(data)["payload"]
    return events


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


def test_reconstructed_events_match_live_context_and_terminal_telemetry() -> None:
    repo = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
    )

    result = loop.run("Which pickup zones have the most trips?")
    live_events = {
        event.event_type: event.payload
        for event in publisher.get_events_for_run(result.run_id)
    }
    reconstructed_events = _sse_events(
        TestClient(create_app(state_repository=repo))
        .get(f"/api/runs/{result.run_id}/events")
        .text
    )

    for event_type, keys in {
        "context.reduced": {"query_id", "row_count", "working_context"},
        "run.completed": {
            "status",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_cost_usd",
            "failure_code",
            "latency_ms",
            "end_to_end_latency_ms",
            "proposal_llm_latency_ms",
            "tool_latency_ms",
            "final_answer_llm_latency_ms",
            "ttft",
            "telemetry",
        },
    }.items():
        assert keys <= reconstructed_events[event_type].keys()
        assert {key: reconstructed_events[event_type][key] for key in keys} == {
            key: live_events[event_type][key] for key in keys
        }


def test_reconstructed_failed_terminal_event_matches_live_contract() -> None:
    class FailingLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: dict[str, object]
        ) -> ToolProposalResult:
            raise LLMProviderError(retryable=True)

    repo = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    loop = OrchestrationLoop(
        llm_client=FailingLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
    )

    with pytest.raises(ValueError):
        loop.run("Which pickup zones have the most trips?")

    run_id = next(iter(repo._runs))
    live_payload = next(
        event.payload
        for event in publisher.get_events_for_run(run_id)
        if event.event_type == "run.failed"
    )
    reconstructed_payload = _sse_events(
        TestClient(create_app(state_repository=repo))
        .get(f"/api/runs/{run_id}/events")
        .text
    )["run.failed"]

    assert reconstructed_payload == live_payload


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
        "answer.delta",
        "answer.completed",
        "llm.completed",
        "run.completed",
    ]
    assert event_types == expected_order


def test_orchestration_loop_emits_provider_deltas_and_truthful_ttft(
) -> None:
    class StreamingLLMClient(LocalFakeLLMClient):
        def stream_answer_with_query_result(
            self,
            prompt: str,
            query_result: Mapping[str, object],
            on_delta: Callable[[str], None],
        ) -> LLMResult:
            on_delta("JFK ")
            on_delta("leads.")
            return LLMResult(
                text="JFK leads.",
                model_id="amazon.nova-micro-v1:0",
                input_tokens=4,
                output_tokens=2,
                latency_ms=300,
            )

    monotonic_values = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 0.9])
    repo = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    loop = OrchestrationLoop(
        llm_client=StreamingLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
        monotonic_factory=lambda: next(monotonic_values),
    )

    result = loop.run("Which pickup zones have the most trips?")

    events = publisher.get_events_for_run(result.run_id)
    answer_events = [event for event in events if event.event_type.startswith("answer.")]
    assert [(event.event_type, event.payload) for event in answer_events] == [
        ("answer.delta", {"delta": "JFK "}),
        ("answer.delta", {"delta": "leads."}),
        ("answer.completed", {"answer": "JFK leads."}),
    ]
    completed_payload = next(
        event.payload for event in events if event.event_type == "run.completed"
    )
    assert completed_payload["ttft"] == {
        "available": True,
        "latency_ms": 150,
        "source": "provider_stream",
    }
    assert repo.list_messages(result.conversation_id)[-1].content == "JFK leads."


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

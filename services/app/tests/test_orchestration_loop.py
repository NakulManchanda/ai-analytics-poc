from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest
from app.config import DEFAULT_MODEL_ID
from app.llm import LLMProviderError, LocalFakeLLMClient, ToolProposalResult
from app.orchestration import (
    ExecutionBudgets,
    OrchestrationLoop,
)
from app.state import InMemoryStateRepository


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


def test_orchestration_loop_normal_completion() -> None:
    repo = InMemoryStateRepository()
    llm = LocalFakeLLMClient()
    mcp = FakeMCPClient()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "completed"
    assert result.query_id == "fake-query-123"
    assert result.tool_call_id is not None
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.estimated_cost_usd > 0
    assert "JFK Airport" in result.answer

    # Verify Durable State records
    conv = repo.get_conversation(result.conversation_id)
    assert conv is not None

    messages = repo.list_messages(result.conversation_id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "completed"

    steps = repo.list_run_steps(result.run_id)
    assert len(steps) == 4
    assert [s.step_type for s in steps] == [
        "llm_proposal",
        "tool_call",
        "context_reduced",
        "llm_final_answer",
    ]
    telemetry = run.metadata["telemetry"]
    assert telemetry["end_to_end_latency_ms"] >= 0
    assert telemetry["proposal_llm_latency_ms"] == steps[0].duration_ms
    assert telemetry["tool_latency_ms"] == steps[1].duration_ms
    assert telemetry["final_answer_llm_latency_ms"] == steps[3].duration_ms
    assert telemetry["ttft"] == {
        "available": False,
        "reason": "non_streaming_blocking",
    }
    assert steps[2].metadata["working_context"]["stored_message_count"] == 1


def test_orchestration_loop_persists_failed_run_for_non_budget_provider_error() -> None:
    class RecordingRepository(InMemoryStateRepository):
        updated_run = None

        def update_run(self, run):  # type: ignore[no-untyped-def]
            self.updated_run = run
            return super().update_run(run)

    repo = RecordingRepository()

    class FailingLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: Mapping[str, object]
        ) -> ToolProposalResult:
            raise LLMProviderError(retryable=True)

    loop = OrchestrationLoop(
        llm_client=FailingLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    with pytest.raises(ValueError):
        loop.run("Which pickup zones have the most trips?")

    run = repo.updated_run
    assert run is not None
    assert run.status == "failed"
    assert run.failure_code == "llm_provider_error"
    assert run.completed_at is not None
    assert run.metadata["telemetry"]["ttft"] == {
        "available": False,
        "reason": "final_answer_not_started",
    }


def test_orchestration_loop_max_iterations_budget_exceeded() -> None:
    repo = InMemoryStateRepository()
    llm = LocalFakeLLMClient()
    mcp = FakeMCPClient()

    budgets = ExecutionBudgets(max_iterations=0)
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"
    assert result.failure_code == "budget_exceeded"

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "budget_exceeded"


def test_orchestration_loop_max_llm_calls_budget_exceeded() -> None:
    repo = InMemoryStateRepository()
    llm = LocalFakeLLMClient()
    mcp = FakeMCPClient()

    budgets = ExecutionBudgets(max_llm_calls=1)  # Needs 2 calls (proposal + answer)
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"
    assert result.failure_code == "budget_exceeded"


def test_orchestration_loop_max_tool_calls_budget_exceeded() -> None:
    repo = InMemoryStateRepository()
    llm = LocalFakeLLMClient()
    mcp = FakeMCPClient()

    budgets = ExecutionBudgets(max_tool_calls=0)
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"
    assert result.failure_code == "budget_exceeded"


def test_orchestration_loop_timeout_deadline_exceeded() -> None:
    repo = InMemoryStateRepository()

    class SlowLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: Mapping[str, object]
        ) -> ToolProposalResult:
            time.sleep(0.05)
            return super().propose_taxi_query(prompt, schema)

    budgets = ExecutionBudgets(timeout_seconds=0.01)
    loop = OrchestrationLoop(
        llm_client=SlowLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"
    assert result.failure_code == "budget_exceeded"


def test_orchestration_loop_max_input_tokens_exceeded() -> None:
    repo = InMemoryStateRepository()
    budgets = ExecutionBudgets(max_input_tokens=2)
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("A long query with many input tokens exceeding limit")
    assert result.status == "budget_exceeded"


def test_orchestration_loop_max_cost_exceeded() -> None:
    repo = InMemoryStateRepository()
    budgets = ExecutionBudgets(max_estimated_cost_usd=0.0000001)
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"


def test_orchestration_loop_max_tool_bytes_exceeded() -> None:
    repo = InMemoryStateRepository()
    budgets = ExecutionBudgets(max_tool_result_bytes=10)
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        budgets=budgets,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "budget_exceeded"


def test_prepare_run_durably_creates_state_and_publishes_received_before_execute() -> (
    None
):
    """prepare_run must persist conversation, user message, and in-progress run, then
    publish run.received — all before execute() is ever called."""

    repo = InMemoryStateRepository()
    received_events: list[str] = []

    class CapturingPublisher:
        def publish(self, evt: Any) -> None:
            received_events.append(evt.event_type)

    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=CapturingPublisher(),  # type: ignore[arg-type]
    )

    submission = loop.prepare_run("Which pickup zones lead?")

    # Durable state must exist immediately, before execute()
    conv = repo.get_conversation(submission.conversation_id)
    assert conv is not None

    messages = repo.list_messages(submission.conversation_id)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content == "Which pickup zones lead?"
    assert messages[0].message_id == submission.message_id

    run = repo.get_run(submission.run_id)
    assert run is not None
    assert run.status == "in_progress"
    assert run.conversation_id == submission.conversation_id
    assert run.message_id == submission.message_id

    # run.received must be published before execute() returns
    assert "run.received" in received_events

    # execute() must complete the run using prepare_run's state
    loop.execute(submission)

    completed_run = repo.get_run(submission.run_id)
    assert completed_run is not None
    assert completed_run.status in ("completed", "budget_exceeded", "failed")
    # The assistant answer message must have been persisted
    all_messages = repo.list_messages(submission.conversation_id)
    assert len(all_messages) == 2
    assert all_messages[1].role == "assistant"


def test_orchestration_loop_invalid_tool_proposal_rejected() -> None:
    repo = InMemoryStateRepository()

    class InvalidToolLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: Mapping[str, object]
        ) -> ToolProposalResult:
            return ToolProposalResult(
                name="non_existent_tool",
                arguments={"unknown": "value"},
                model_id=DEFAULT_MODEL_ID,
                input_tokens=10,
                output_tokens=10,
                latency_ms=0,
            )

    loop = OrchestrationLoop(
        llm_client=InvalidToolLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    with pytest.raises(ValueError, match="Invalid tool proposal"):
        loop.run("Which pickup zones have the most trips?")


def test_parse_query_proposal_average_trip_metrics() -> None:
    from app.orchestration.loop import parse_query_proposal

    # Empty dict arguments
    p1 = ToolProposalResult(
        name="average_trip_metrics",
        arguments={},
        model_id=DEFAULT_MODEL_ID,
        input_tokens=10,
        output_tokens=10,
        latency_ms=0,
    )
    assert parse_query_proposal(p1) == ("average_trip_metrics", {})

    # None arguments
    p2 = ToolProposalResult(
        name="average_trip_metrics",
        arguments=None,
        model_id=DEFAULT_MODEL_ID,
        input_tokens=10,
        output_tokens=10,
        latency_ms=0,
    )
    assert parse_query_proposal(p2) == ("average_trip_metrics", {})

    # Valid region_name
    p3 = ToolProposalResult(
        name="average_trip_metrics",
        arguments={"region_name": "Queens"},
        model_id=DEFAULT_MODEL_ID,
        input_tokens=10,
        output_tokens=10,
        latency_ms=0,
    )
    assert parse_query_proposal(p3) == (
        "average_trip_metrics",
        {"region_name": "Queens"},
    )

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest
from app.config import DEFAULT_MODEL_ID
from app.llm import LocalFakeLLMClient, ToolProposalResult
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
    assert len(steps) == 3
    assert [s.step_type for s in steps] == [
        "llm_proposal",
        "tool_call",
        "llm_final_answer",
    ]


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

from typing import Any

import pytest
from app.config import LLMConfigurationError, Settings
from app.llm import LLMResult, LocalFakeLLMClient


def test_agent_strategy_defaults_to_manual_and_reads_crewai(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_STRATEGY", raising=False)
    assert Settings.from_environment().agent_strategy == "manual"

    monkeypatch.setenv("AGENT_STRATEGY", "crewai")
    assert Settings.from_environment().agent_strategy == "crewai"


def test_agent_strategy_rejects_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STRATEGY", "autonomous")

    with pytest.raises(LLMConfigurationError, match="manual or crewai"):
        Settings.from_environment()


class RecordingLLM:
    model_id = "test-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            text=f"answer {len(self.prompts)}",
            model_id=self.model_id,
            input_tokens=7,
            output_tokens=3,
            latency_ms=11,
            finish_reason="stop",
        )


def test_gateway_llm_delegates_string_and_message_inputs_to_owned_client() -> None:
    from app.orchestration.crewai_strategy import GatewayLLM

    client = RecordingLLM()
    observed: list[LLMResult] = []
    gateway = GatewayLLM(client, on_call=observed.append)

    assert gateway.call("first prompt") == "answer 1"
    assert (
        gateway.call(
            [
                {"role": "system", "content": "system rules"},
                {"role": "user", "content": "second prompt"},
            ]
        )
        == "answer 2"
    )

    assert client.prompts == [
        "first prompt",
        "system: system rules\n\nuser: second prompt",
    ]
    assert [result.text for result in observed] == ["answer 1", "answer 2"]
    assert gateway.supports_function_calling() is False


def test_gateway_llm_rejects_crewai_tool_calls() -> None:
    from app.orchestration.crewai_strategy import GatewayLLM

    gateway = GatewayLLM(RecordingLLM(), on_call=lambda _result: None)

    with pytest.raises(ValueError, match="does not expose tools"):
        gateway.call("prompt", tools=[{"type": "function"}])


def test_two_agent_answer_is_bounded_and_returns_only_writer_output() -> None:
    from app.orchestration.crewai_strategy import run_two_agent_answer

    client = RecordingLLM()

    result = run_two_agent_answer(
        llm_client=client,
        question="Which zone is busiest?",
        governed_result={
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["Midtown", 42]],
            "row_count": 1,
            "query_id": "query-1",
            "truncated": False,
        },
    )

    assert result.text == "answer 2"
    assert [call.text for call in result.calls] == ["answer 1", "answer 2"]
    assert len(client.prompts) == 2
    assert "Midtown" in client.prompts[0]
    assert "answer 1" in client.prompts[1]
    assert "You write one short, direct answer" not in result.text


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


class CountingFakeLLMClient(LocalFakeLLMClient):
    def __init__(self) -> None:
        self.ask_prompts: list[str] = []

    def ask(self, prompt: str) -> LLMResult:
        self.ask_prompts.append(prompt)
        return LLMResult(
            text=f"Crew answer {len(self.ask_prompts)}",
            model_id=self.model_id,
            input_tokens=15,
            output_tokens=8,
            latency_ms=12,
            finish_reason="stop",
        )


def test_orchestration_loop_crewai_strategy_end_to_end() -> None:
    from app.orchestration import OrchestrationLoop
    from app.state import InMemoryStateRepository

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        agent_strategy="crewai",
    )

    result = loop.run("What are the top pickup zones by ride count?")

    assert result.status == "completed"
    assert result.answer == "Crew answer 2"
    assert "You write one short, direct answer" not in result.answer

    # Assert exact fake LLM call count: 1 proposal + 2 crew calls (researcher + writer)
    assert len(llm.ask_prompts) == 2
    assert len(result.llm_calls) == 3

    # Verify steps and telemetry shape
    step_types = [s.step_type for s in result.steps]
    assert step_types == [
        "llm_proposal",
        "tool_call",
        "context_reduced",
        "crewai_crew",
    ]

    last_step = result.steps[-1]
    assert last_step.step_type == "crewai_crew"
    assert last_step.output_summary == "answer: Crew answer 2"
    assert last_step.llm_call_id == result.llm_calls[-1].llm_call_id

    # Verify all LLMCall records have required telemetry fields
    for call in result.llm_calls:
        meta = call.to_metadata()
        assert "model_id" in meta
        assert "input_tokens" in meta
        assert "output_tokens" in meta
        assert "latency_ms" in meta
        assert "finish_reason" in meta
        assert "cost_usd" in meta

    # Verify durable state
    conv = repo.get_conversation(result.conversation_id)
    assert conv is not None
    messages = repo.list_messages(result.conversation_id)
    assert len(messages) == 3
    assert messages[0].role == "user"
    assert messages[1].role == "tool"
    assert messages[2].role == "assistant"
    assert messages[2].content == "Crew answer 2"

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "completed"
    assert len(run.metadata["llm_calls"]) == 3


def test_orchestration_loop_crewai_strategy_budget_enforcement() -> None:
    from app.orchestration import ExecutionBudgets, OrchestrationLoop
    from app.state import InMemoryStateRepository

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()

    # Proposal is call 1, researcher is call 2, writer would be call 3 -> budget exceeded
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=ExecutionBudgets(max_llm_calls=2),
        agent_strategy="crewai",
    )

    result = loop.run("What are the top pickup zones by ride count?")
    assert result.status == "budget_exceeded"

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "budget_exceeded"


def test_orchestration_loop_crewai_strategy_invalid_call_count_failure(
    monkeypatch,
) -> None:
    from app.orchestration import OrchestrationError, OrchestrationLoop, crewai_strategy
    from app.state import InMemoryStateRepository

    def failing_answer(**kwargs):
        raise RuntimeError("expected exactly 2 CrewAI model calls, got 1")

    monkeypatch.setattr(crewai_strategy, "run_two_agent_answer", failing_answer)

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        agent_strategy="crewai",
    )

    with pytest.raises(OrchestrationError) as exc_info:
        loop.run("What are the top pickup zones by ride count?")

    assert exc_info.value.code == "strategy_execution_error"


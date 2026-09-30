from typing import Any

import pytest
from app.config import LLMConfigurationError, Settings
from app.events import InMemoryEventPublisher
from app.llm import LLMProviderError, LLMResult, LocalFakeLLMClient, ServeLLMClient
from app.prefix import PrefixPartition, create_prefix_partition


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


def test_two_agent_answer_correlated_growing_prefixes() -> None:
    from app.orchestration.crewai_strategy import run_two_agent_answer

    captured_invocations: list[dict[str, Any]] = []

    def mock_invoke(
        role: str, partition: PrefixPartition, agent_step: int
    ) -> LLMResult:
        captured_invocations.append(
            {
                "role": role,
                "partition": partition,
                "agent_step": agent_step,
            }
        )
        return LLMResult(
            text=f"Summary for {role}",
            model_id="test-model",
            input_tokens=20,
            output_tokens=10,
            latency_ms=15,
            finish_reason="stop",
        )

    res = run_two_agent_answer(
        question="Busiest hours?",
        governed_result={"rows": [[17, 500]]},
        invoke_model=mock_invoke,
        conversation_id="conv-test-123",
        start_step=2,
    )

    assert len(captured_invocations) == 2
    researcher_call = captured_invocations[0]
    writer_call = captured_invocations[1]

    assert researcher_call["role"] == "Researcher"
    assert researcher_call["agent_step"] == 2
    assert (
        "Observation (query_result)" in researcher_call["partition"].conversation_shared
    )

    assert writer_call["role"] == "Writer"
    assert writer_call["agent_step"] == 3
    # Check growing prefix: writer's conversation_shared contains
    # researcher's observation plus research summary
    assert (
        researcher_call["partition"].conversation_shared
        in writer_call["partition"].conversation_shared
    )
    assert (
        "Research summary: Summary for Researcher"
        in writer_call["partition"].conversation_shared
    )

    # Shared global prompt is identical
    assert (
        researcher_call["partition"].global_shared
        == writer_call["partition"].global_shared
    )
    # Prefix IDs are valid hashes
    assert len(researcher_call["partition"].prefix_id) == 16
    assert len(writer_call["partition"].prefix_id) == 16
    assert researcher_call["partition"].prefix_id != writer_call["partition"].prefix_id
    assert res.text == "Summary for Writer"


def test_serve_llm_client_execute_partitioned_call_headers(monkeypatch) -> None:
    captured_requests: list[dict[str, Any]] = []

    class DummyResponse:
        status_code = 200

        def json(self):
            return {
                "id": "cmpl-1",
                "model": "vllm-model",
                "choices": [
                    {
                        "message": {"content": "vLLM test answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 15, "completion_tokens": 5},
            }

    class DummyHttpClient:
        def post(self, url, json=None, headers=None):
            captured_requests.append({"url": url, "json": json, "headers": headers})
            return DummyResponse()

    client = ServeLLMClient(
        gateway_url="http://localhost:8000/v1/chat/completions",
        model_id="vllm-model",
        http_client=DummyHttpClient(),  # type: ignore[arg-type]
    )

    part = create_prefix_partition(
        global_shared="sys prompt",
        conversation_shared="conv shared",
        unique_suffix="user query",
    )

    result = client.execute_partitioned_call(
        partition=part,
        conversation_id="conv-abc",
        agent_step=2,
    )

    assert result.text == "vLLM test answer"
    assert len(captured_requests) == 1
    req = captured_requests[0]
    headers = req["headers"]
    assert headers["x-conversation-id"] == "conv-abc"
    assert headers["x-agent-step"] == "2"
    assert headers["x-prefix-id"] == part.prefix_id
    assert int(headers["x-estimated-prompt-tokens"]) > 0


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
        self.partition_calls: list[dict[str, Any]] = []

    def execute_partitioned_call(
        self,
        partition: PrefixPartition,
        *,
        conversation_id: str | None = None,
        agent_step: int = 1,
    ) -> LLMResult:
        self.partition_calls.append(
            {
                "partition": partition,
                "conversation_id": conversation_id,
                "agent_step": agent_step,
            }
        )
        return LLMResult(
            text=f"Crew answer {len(self.partition_calls)}",
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
    publisher = InMemoryEventPublisher()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
        agent_strategy="crewai",
    )

    result = loop.run("What are the top pickup zones by ride count?")

    assert result.status == "completed"
    assert result.answer == "Crew answer 2"
    assert "You write one short, direct answer" not in result.answer

    # 1 proposal + 2 partitioned crew calls (researcher + writer)
    assert len(llm.partition_calls) == 2
    assert llm.partition_calls[0]["agent_step"] == 2
    assert llm.partition_calls[1]["agent_step"] == 3
    assert len(result.llm_calls) == 3

    # Verify per-call steps
    step_types = [s.step_type for s in result.steps]
    assert step_types == [
        "llm_proposal",
        "tool_call",
        "context_reduced",
        "crewai_researcher",
        "crewai_writer",
    ]

    researcher_step = result.steps[-2]
    assert researcher_step.step_type == "crewai_researcher"
    assert researcher_step.llm_call_id == result.llm_calls[-2].llm_call_id

    writer_step = result.steps[-1]
    assert writer_step.step_type == "crewai_writer"
    assert writer_step.output_summary == "output: Crew answer 2"
    assert writer_step.llm_call_id == result.llm_calls[-1].llm_call_id

    # Verify all LLMCall records have required telemetry fields
    for call in result.llm_calls:
        meta = call.to_metadata()
        assert "model_id" in meta
        assert "input_tokens" in meta
        assert "output_tokens" in meta
        assert "latency_ms" in meta
        assert "finish_reason" in meta
        assert "cost_usd" in meta

    # Verify per-call events emitted
    event_types = [e.event_type for e in publisher.events]
    assert event_types.count("llm.started") == 3  # proposal, researcher, writer
    assert event_types.count("llm.completed") == 3

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


def test_orchestration_loop_crewai_strategy_pre_call_budget_enforcement() -> None:
    from app.orchestration import ExecutionBudgets, OrchestrationLoop
    from app.state import InMemoryStateRepository

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()

    # Proposal is call 1, researcher is call 2. Writer pre-call check prevents call 3.
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=ExecutionBudgets(max_llm_calls=2),
        agent_strategy="crewai",
    )

    result = loop.run("What are the top pickup zones by ride count?")
    assert result.status == "budget_exceeded"

    # Pre-call budget check: writer was prevented BEFORE calling provider
    assert len(llm.partition_calls) == 1  # Only researcher was called!

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "budget_exceeded"
    # Completed researcher call telemetry is retained
    assert len(run.metadata["llm_calls"]) == 2


def test_orchestration_loop_crewai_strategy_pre_call_cancellation() -> None:
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

    # Cancel the run when researcher executes
    original_exec = llm.execute_partitioned_call

    def cancel_during_researcher(partition, conversation_id=None, agent_step=1):
        res = original_exec(
            partition, conversation_id=conversation_id, agent_step=agent_step
        )
        # Mark run as cancelled
        runs = list(repo._runs.values())
        if runs:
            loop.request_cancellation(runs[0].run_id)
        return res

    llm.execute_partitioned_call = cancel_during_researcher  # type: ignore[method-assign]

    result = loop.run("What are the top pickup zones by ride count?")
    assert result.status == "cancelled"

    # Writer was prevented before calling provider
    assert len(llm.partition_calls) == 1

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "cancelled"
    # Retains researcher call telemetry
    assert len(run.metadata["llm_calls"]) == 2


def test_orchestration_loop_crewai_strategy_provider_failure_durable_handling() -> None:
    from app.orchestration import OrchestrationError, OrchestrationLoop
    from app.state import InMemoryStateRepository

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()
    publisher = InMemoryEventPublisher()

    # Researcher succeeds, but Writer fails with LLMProviderError
    call_count = 0

    def fail_on_writer(partition, conversation_id=None, agent_step=1):
        nonlocal call_count
        call_count += 1
        if call_count >= 2 or agent_step == 3:
            raise LLMProviderError(retryable=True, code="vllm_gateway_unavailable")
        return LLMResult(
            text="Research summary text",
            model_id=llm.model_id,
            input_tokens=10,
            output_tokens=5,
            latency_ms=10,
        )

    llm.execute_partitioned_call = fail_on_writer  # type: ignore[method-assign]

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
        agent_strategy="crewai",
    )

    with pytest.raises(OrchestrationError) as exc_info:
        loop.run("What are the top pickup zones by ride count?")

    assert exc_info.value.code == "vllm_gateway_unavailable"

    # Verify durable state is updated to failed (NOT left stuck as running!)
    runs = list(repo._runs.values())
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert run.failure_code == "vllm_gateway_unavailable"

    # Completed researcher call telemetry was retained
    assert len(run.metadata["llm_calls"]) == 2  # proposal + researcher
    assert any(
        s.step_type == "crewai_researcher" for s in repo.list_run_steps(run.run_id)
    )

    # Verify terminal run.failed event was emitted
    event_types = [e.event_type for e in publisher.events]
    assert "run.failed" in event_types


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
    runs = list(repo._runs.values())
    assert runs[0].status == "failed"


def test_orchestration_loop_crewai_strategy_in_flight_cancellation() -> None:
    import threading

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

    provider_blocked = threading.Event()
    provider_unblock = threading.Event()
    provider_returned = threading.Event()

    original_exec = llm.execute_partitioned_call

    def blocking_researcher(partition, conversation_id=None, agent_step=1):
        if agent_step == 2:
            provider_blocked.set()
            provider_unblock.wait(timeout=5.0)
            provider_returned.set()
        return original_exec(
            partition, conversation_id=conversation_id, agent_step=agent_step
        )

    llm.execute_partitioned_call = blocking_researcher  # type: ignore[method-assign]

    run_result = None
    loop_error = None

    def run_worker():
        nonlocal run_result, loop_error
        try:
            run_result = loop.run("What are the top pickup zones by ride count?")
        except Exception as err:
            loop_error = err

    worker_thread = threading.Thread(target=run_worker)
    worker_thread.start()

    assert provider_blocked.wait(timeout=5.0), "Provider never entered blocked state"

    runs = list(repo._runs.values())
    assert runs, "Run was not initialized in repository"
    run_id = runs[0].run_id
    loop.request_cancellation(run_id)

    worker_thread.join(timeout=3.0)
    assert (
        not worker_thread.is_alive()
    ), "Worker thread did not terminate upon cancellation"
    assert (
        not provider_returned.is_set()
    ), "Provider returned before cancellation aborted run"

    provider_unblock.set()

    assert loop_error is None
    assert run_result is not None
    assert run_result.status == "cancelled"

    run = repo.get_run(run_id)
    assert run is not None
    assert run.status == "cancelled"


def test_orchestration_loop_crewai_strategy_post_call_token_budget_exceeded() -> None:
    from app.orchestration import ExecutionBudgets, OrchestrationLoop
    from app.state import InMemoryStateRepository

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()

    # Proposal call consumes 5 input tokens.
    # Researcher call consumes 8 input tokens (total 13 > 10).
    # Post-call tracker.record_llm_call will raise BudgetExceededError.
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        budgets=ExecutionBudgets(max_input_tokens=10),
        agent_strategy="crewai",
    )

    result = loop.run("What are the top pickup zones by ride count?")
    assert result.status == "budget_exceeded"

    # Writer was never called
    assert len(llm.partition_calls) == 1

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.status == "budget_exceeded"
    # Completed researcher call telemetry is retained despite post-call budget failure!
    assert len(run.metadata["llm_calls"]) == 2
    steps = repo.list_run_steps(result.run_id)
    step_types = [s.step_type for s in steps]
    assert "crewai_researcher" in step_types


def test_orchestration_loop_crewai_strategy_sse_events_and_ttft() -> None:
    from app.orchestration import OrchestrationLoop
    from app.state import InMemoryStateRepository

    repo = InMemoryStateRepository()
    llm = CountingFakeLLMClient()
    mcp = FakeMCPClient()
    publisher = InMemoryEventPublisher()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
        agent_strategy="crewai",
    )

    result = loop.run("What are the top pickup zones by ride count?")
    assert result.status == "completed"

    event_types = [e.event_type for e in publisher.events]
    assert "run.received" in event_types
    assert "llm.started" in event_types
    assert "llm.completed" in event_types
    assert "tool.started" in event_types
    assert "tool.completed" in event_types
    assert "answer.completed" in event_types
    assert "run.completed" in event_types

    # Intentional architecture decision: CrewAI delivers final answer via answer.completed
    # without progressive answer.delta tokens to prevent internal agent scratchpad leakage
    assert "answer.delta" not in event_types

    assert result.telemetry["ttft"]["available"] is False
    assert result.telemetry["ttft"]["reason"] == "non_streaming_blocking"


def test_ecs_terraform_agent_strategy_variable() -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    var_tf = repo_root / "infra" / "terraform" / "variables.tf"
    ecs_tf = repo_root / "infra" / "terraform" / "ecs.tf"

    var_content = var_tf.read_text(encoding="utf-8")
    ecs_content = ecs_tf.read_text(encoding="utf-8")

    assert 'variable "agent_strategy"' in var_content
    assert 'default     = "manual"' in var_content
    assert 'contains(["manual", "crewai"], var.agent_strategy)' in var_content

    assert 'name  = "AGENT_STRATEGY"' in ecs_content
    assert "value = var.agent_strategy" in ecs_content

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

    result = loop.run("What are the top pickup zones by ride count?")
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
    # user -> tool (persisted governed tool observation, D18) -> assistant.
    assert len(messages) == 3
    assert messages[0].role == "user"
    assert messages[1].role == "tool"
    assert messages[2].role == "assistant"

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
    # user + the persisted tool observation (D18), before the assistant answer.
    assert steps[2].metadata["working_context"]["stored_message_count"] == 2


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
        loop.run("What are the top pickup zones by ride count?")

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

    result = loop.run("What are the top pickup zones by ride count?")
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

    # Non-catalogued phrasing: a catalogue hit would skip propose_taxi_query entirely
    # (and its artificial sleep), so this prompt must not match any query_catalogue entry.
    result = loop.run("Give me a slow deliberately-uncatalogued timeout probe question")
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
    # The assistant answer message must have been persisted (after the
    # user message and the persisted tool observation, D18).
    all_messages = repo.list_messages(submission.conversation_id)
    assert len(all_messages) == 3
    assert all_messages[1].role == "tool"
    assert all_messages[2].role == "assistant"


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
        loop.run("What are the top pickup zones by ride count?")


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


def test_orchestration_loop_maps_no_tool_call_to_orchestration_error() -> None:
    """#115 slice B: the loop must map the typed no_tool_call failure from the LLM
    client into its existing OrchestrationError path (failure_code == 'no_tool_call'),
    with no keyword-selected tool ever substituted."""

    class NoToolCallLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: Mapping[str, object]
        ) -> ToolProposalResult:
            raise LLMProviderError(retryable=False, code="no_tool_call")

    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=NoToolCallLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    with pytest.raises(ValueError):
        loop.run("What are the top pickup zones by ride count?")

    runs = [
        repo.get_run(rid)
        for rid in getattr(repo, "_runs", {})  # type: ignore[attr-defined]
    ]
    failed_runs = [r for r in runs if r is not None and r.status == "failed"]
    assert failed_runs
    assert failed_runs[0].failure_code == "no_tool_call"


def test_orchestration_loop_serve_mode_records_served_model_and_self_hosted_cost() -> (
    None
):
    """#115 slice B: serve-mode runs must carry the actual served model id and a
    non-Bedrock cost, never the Bedrock default model id or Bedrock-rate cost."""
    import httpx
    from app.llm import ServeLLMClient

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        payload = _json.loads(request.content)
        if "tools" in payload:
            return httpx.Response(
                200,
                json={
                    "model": "Qwen/Qwen3-0.6B",
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "query_taxi_data",
                                            "arguments": _json.dumps(
                                                {
                                                    "analysis": "top_pickup_zones",
                                                    "limit": 5,
                                                }
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 6},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {
                        "message": {"content": "JFK Airport leads with 1500 trips."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 60, "completion_tokens": 12},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )

    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=serve_client,
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "completed"
    assert result.estimated_cost_usd == 0.0

    run = repo.get_run(result.run_id)
    assert run is not None
    assert run.model == "Qwen/Qwen3-0.6B"
    assert run.model != DEFAULT_MODEL_ID
    assert run.estimated_cost_usd == 0.0
    assert run.metadata["cost_source"] == "self_hosted"


def test_orchestration_loop_serve_mode_no_tool_call_keeps_served_model_and_finish_reason() -> (
    None
):
    """#138 review major: a no_tool_call failure in serve mode must not fall back
    to the Bedrock default model id, and must preserve the failed call's
    finish_reason/usage telemetry instead of discarding it."""
    import httpx
    from app.llm import ServeLLMClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "no tool used"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 6},
            },
        )

    transport = httpx.MockTransport(handler)
    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=transport),
    )

    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=serve_client,
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    with pytest.raises(ValueError):
        loop.run("What are the top pickup zones by ride count?")

    runs = [
        repo.get_run(rid)
        for rid in getattr(repo, "_runs", {})  # type: ignore[attr-defined]
    ]
    failed_runs = [r for r in runs if r is not None and r.status == "failed"]
    assert failed_runs
    failed_run = failed_runs[0]
    assert failed_run.failure_code == "no_tool_call"
    # The served model id must be preserved, not the Bedrock default.
    assert failed_run.model == "Qwen/Qwen3-0.6B"
    assert failed_run.model != DEFAULT_MODEL_ID
    llm_calls = failed_run.metadata["llm_calls"]
    assert llm_calls
    assert llm_calls[0]["model_id"] == "Qwen/Qwen3-0.6B"
    assert llm_calls[0]["finish_reason"] == "stop"
    assert llm_calls[0]["error_code"] == "no_tool_call"
    assert llm_calls[0]["input_tokens"] == 30
    assert llm_calls[0]["output_tokens"] == 6


class ExtendedFakeMCPClient(FakeMCPClient):
    """Adds the slice A extended governed tools for the catalogue/dispatch tests."""

    def describe_taxi_dataset(
        self, *, include_column_stats: bool = False
    ) -> dict[str, Any]:
        return {
            "query_class": "describe",
            "row_count": 12345,
            "min_pickup_datetime": "2024-01-01T00:00:00",
            "max_pickup_datetime": "2024-01-31T23:59:59",
            "columns": [{"name": "PULocationID", "type": "BIGINT"}],
            "supported_dimensions": ["pickup_borough"],
            "supported_measures": ["trip_count"],
            "code_dictionaries": {"payment_type": {"1": "credit_card"}},
            "tip_rate_semantics": "tip / fare_amount",
            "airport_trip_rule": "PULocationID in (...)",
            "valid_records_rule": "fare_amount >= 0",
            "truncated": False,
            "query_id": "fake-describe-1",
        }

    def average_trip_metrics(self, *, region_name: str | None = None) -> dict[str, Any]:
        return {
            "columns": [
                "region_name",
                "trip_count",
                "average_trip_distance",
                "average_fare_amount",
            ],
            "rows": [["Manhattan", 900, 2.1, 11.5]],
            "row_count": 1,
            "execution_duration_ms": 8,
            "query_id": "fake-avg-1",
            "truncated": False,
        }


def test_catalogue_hit_skips_model_and_records_tool_source_catalogue() -> None:
    """A catalogue-matched question must skip the model tool-call entirely and
    record tool_source='catalogue' on the proposal step."""

    class ExplodingProposalLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(self, prompt, schema, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(
                "model tool proposal must not be called on a catalogue hit"
            )

    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=ExplodingProposalLLMClient(),
        mcp_client=ExtendedFakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    result = loop.run("Which pickup zones have the most trips?")
    assert result.status == "completed"

    steps = repo.list_run_steps(result.run_id)
    proposal_step = steps[0]
    assert proposal_step.step_type == "llm_proposal"
    assert proposal_step.metadata["tool_source"] == "catalogue"


def test_non_catalogue_question_records_tool_source_model_and_gets_prior_turn_context() -> (
    None
):
    """A non-catalogue (follow-up-style) question must go through the model and
    record tool_source='model', while still receiving real prior-turn context
    via the renderer for a serve-mode client."""
    import httpx
    from app.llm import ServeLLMClient

    captured_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        payload = _json.loads(request.content)
        captured_payloads.append(payload)
        if "tools" in payload:
            return httpx.Response(
                200,
                json={
                    "model": "Qwen/Qwen3-0.6B",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "query_taxi_data",
                                            "arguments": '{"analysis": "top_pickup_zones", "limit": 5}',
                                        }
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-0.6B",
                "choices": [
                    {"message": {"content": "Answer."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
        )

    serve_client = ServeLLMClient(
        gateway_url="http://localhost:18080/serve",
        model_id="Qwen/Qwen3-0.6B",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=serve_client,
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
    )

    # A non-catalogue, follow-up-style question (D19).
    result = loop.run("Compare that with the second highest zone")
    assert result.status == "completed"

    steps = repo.list_run_steps(result.run_id)
    proposal_step = steps[0]
    assert proposal_step.metadata["tool_source"] == "model"

    # The proposal call's rendered messages must have real conversation
    # history (system + prior stored messages + new question), i.e. more
    # than the old single-turn [system, user] shape.
    proposal_payload = captured_payloads[0]
    assert len(proposal_payload["messages"]) >= 2
    assert "Compare that with the second highest zone" in (
        proposal_payload["messages"][-1]["content"]
    )


def test_extended_governed_tool_runs_end_to_end_through_the_loop() -> None:
    """A catalogue hit naming one of the slice A extended tools (not just
    query_taxi_data/average_trip_metrics) must dispatch through the loop,
    get sanitized, and reach the final answer -- not be rejected as an
    'invalid tool proposal'."""
    repo = InMemoryStateRepository()
    llm = LocalFakeLLMClient()
    mcp = ExtendedFakeMCPClient()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=repo,
    )

    # This chip question is catalogued to describe_taxi_dataset.
    result = loop.run(
        "What date range, row count, and columns does this taxi dataset cover?"
    )
    assert result.status == "completed"

    steps = repo.list_run_steps(result.run_id)
    proposal_step, tool_step = steps[0], steps[1]
    assert proposal_step.metadata["tool_source"] == "catalogue"
    assert tool_step.step_type == "tool_call"
    assert tool_step.status == "completed"
    assert tool_step.tool_name == "describe_taxi_dataset"

    # The persisted tool message carries the full (non-row/column) governed
    # result, not something forced through the row/column-only schema.
    messages = repo.list_messages(result.conversation_id)
    tool_messages = [m for m in messages if m.role == "tool"]
    assert tool_messages
    assert "code_dictionaries" in tool_messages[0].content


def test_extended_tool_model_proposal_is_parsed_and_validated() -> None:
    """parse_query_proposal must accept a model proposal naming one of the 4
    extended governed tools, not just the original 2."""
    from app.llm import ToolProposalResult
    from app.orchestration.loop import parse_query_proposal

    proposal = ToolProposalResult(
        name="aggregate_taxi_data",
        arguments={
            "dimensions": ["pickup_borough"],
            "measures": ["trip_count"],
        },
        model_id="fake",
        input_tokens=1,
        output_tokens=1,
        latency_ms=0,
    )
    parsed = parse_query_proposal(proposal)
    assert parsed is not None
    tool_name, tool_arguments = parsed
    assert tool_name == "aggregate_taxi_data"
    assert tool_arguments["dimensions"] == ["pickup_borough"]
    assert tool_arguments["measures"] == ["trip_count"]
    assert tool_arguments["limit"] == 20

    # Malformed arguments (missing required list fields) must be rejected.
    bad_proposal = ToolProposalResult(
        name="aggregate_taxi_data",
        arguments={"dimensions": "not-a-list", "measures": ["trip_count"]},
        model_id="fake",
        input_tokens=1,
        output_tokens=1,
        latency_ms=0,
    )
    assert parse_query_proposal(bad_proposal) is None

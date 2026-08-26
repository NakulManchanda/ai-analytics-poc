from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from app.events.publisher import InMemoryEventPublisher
from app.llm import LLMClient, LLMResult, ToolProposalResult
from app.mcp_client import DatasetProfileMCPClient
from app.orchestration.loop import OrchestrationLoop
from app.state.repository import InMemoryStateRepository


class FakePublisher(InMemoryEventPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.flags: dict[str, str] = {}

    def _get_client(self) -> FakePublisher:
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.flags[key] = value

    def get(self, key: str) -> str | None:
        return self.flags.get(key)


class MockLLM(LLMClient):
    def __init__(self, deltas: list[str] | None = None) -> None:
        self.deltas = deltas or ["Chunk 1 ", "Chunk 2 ", "Chunk 3"]
        self.propose_called = False
        self.answer_called = False

    def propose_taxi_query(
        self, prompt: str, schema_context: dict[str, Any]
    ) -> ToolProposalResult:
        self.propose_called = True
        return ToolProposalResult(
            name="average_trip_metrics",
            arguments={"region_name": "Manhattan"},
            model_id="mock-llm",
            input_tokens=50,
            output_tokens=20,
            latency_ms=100,
        )

    def stream_answer_with_query_result(
        self,
        prompt: str,
        query_result: dict[str, Any],
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        self.answer_called = True
        accumulated: list[str] = []
        for delta in self.deltas:
            accumulated.append(delta)
            on_delta(delta)
        return LLMResult(
            text="".join(accumulated),
            model_id="mock-llm",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200,
        )

    def answer_with_query_result(
        self, prompt: str, query_result: dict[str, Any]
    ) -> LLMResult:
        return LLMResult(
            text="full answer",
            model_id="mock-llm",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200,
        )


class MockMCP(DatasetProfileMCPClient):
    def __init__(self) -> None:
        self.query_called = False

    def get_dataset_schema(self) -> dict[str, Any]:
        return {
            "columns": ["fare_amount", "trip_distance"],
            "dataset": "nyc-yellow-taxi",
            "month": "2024-01",
        }

    def query_taxi_data(self, analysis: str, limit: int) -> dict[str, Any]:
        self.query_called = True
        return {
            "query_id": "qid_123",
            "row_count": 5,
            "columns": ["fare_amount"],
            "rows": [[15.0]],
            "execution_duration_ms": 10,
            "truncated": False,
        }

    def average_trip_metrics(self, region_name: str | None = None) -> dict[str, Any]:
        return {
            "query_id": "qid_avg",
            "row_count": 1,
            "columns": ["avg_fare"],
            "rows": [[12.5]],
            "execution_duration_ms": 10,
            "truncated": False,
        }


def test_cancellation_before_tool_execution() -> None:
    repo = InMemoryStateRepository()
    pub = FakePublisher()
    llm = MockLLM()
    mcp = MockMCP()

    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,
        state_repository=repo,
        event_publisher=pub,
    )

    submission = loop.prepare_run("Test cancellation")
    # Cancel the run before execution starts
    loop.request_cancellation(submission.run_id)

    result = loop.execute(submission)

    assert result.status == "cancelled"
    assert result.failure_code == "cancelled"
    # Zero MCP tool calls executed
    assert mcp.query_called is False

    # Durable state check
    run = repo.get_run(submission.run_id)
    assert run is not None
    assert run.status == "cancelled"

    # Event stream check
    events = pub.get_events_for_run(submission.run_id)
    event_types = [e.event_type for e in events]
    assert "run.cancel_requested" in event_types
    assert "run.cancelled" in event_types
    assert "tool.started" not in event_types


def test_cancellation_during_answer_delta_streaming() -> None:
    repo = InMemoryStateRepository()
    pub = FakePublisher()
    mcp = MockMCP()

    class CancellingLLM(MockLLM):
        def stream_answer_with_query_result(
            self,
            prompt: str,
            query_result: dict[str, Any],
            on_delta: Callable[[str], None],
        ) -> LLMResult:
            on_delta("First chunk ")
            # Simulate cancellation requested mid-stream after first chunk
            pub.set(f"run:cancel:{run_id_ref[0]}", "1")
            on_delta("Second chunk ")
            pytest.fail("Should not reach after cancelled delta")

    run_id_ref: list[str] = []
    llm = CancellingLLM()
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,
        state_repository=repo,
        event_publisher=pub,
    )

    submission = loop.prepare_run("Stream cancel test")
    run_id_ref.append(submission.run_id)

    result = loop.execute(submission)

    assert result.status == "cancelled"
    assert "First chunk" in result.answer

    # Verify partial message persisted in conversation
    messages = repo.list_messages(submission.conversation_id)
    asst_messages = [m for m in messages if m.role == "assistant"]
    assert len(asst_messages) == 1
    assert asst_messages[0].content == "First chunk Second chunk  [interrupted]"
    assert asst_messages[0].metadata.get("interrupted") is True

    # Verify run.cancelled emitted
    events = pub.get_events_for_run(submission.run_id)
    cancelled_event = next(e for e in events if e.event_type == "run.cancelled")
    assert cancelled_event.payload["status"] == "cancelled"


def test_cancellation_during_mcp_tool_execution() -> None:
    import time

    repo = InMemoryStateRepository()
    pub = FakePublisher()
    llm = MockLLM()

    run_id_ref: list[str] = []

    class SlowMCP(MockMCP):
        def average_trip_metrics(
            self, region_name: str | None = None
        ) -> dict[str, Any]:
            # Simulate cancellation requested while tool is executing
            pub.set(f"run:cancel:{run_id_ref[0]}", "1")
            # Simulate a slow query that would take 2 seconds if not aborted
            time.sleep(2.0)
            return {
                "query_id": "late_query",
                "row_count": 5,
                "columns": ["x"],
                "rows": [[1]],
            }

    mcp = SlowMCP()
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,
        state_repository=repo,
        event_publisher=pub,
    )

    submission = loop.prepare_run("Slow tool cancel test")
    run_id_ref.append(submission.run_id)

    start_time = time.monotonic()
    result = loop.execute(submission)
    elapsed = time.monotonic() - start_time

    assert result.status == "cancelled"
    # Must abort quickly (<0.8s), not waiting for the 2.0s sleep in the slow tool
    assert elapsed < 0.8

    # Verify run.cancelled emitted without tool.completed
    events = pub.get_events_for_run(submission.run_id)
    event_types = [e.event_type for e in events]
    assert "tool.started" in event_types
    assert "run.cancelled" in event_types
    assert "tool.completed" not in event_types

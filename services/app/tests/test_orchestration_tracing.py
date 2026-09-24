from collections.abc import Mapping

import pytest
from app.config import DEFAULT_MODEL_ID, VoiceSettings
from app.llm import LLMProviderError, LocalFakeLLMClient, ToolProposalResult
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode


class FakeMCPClient:
    def get_dataset_schema(self) -> dict[str, object]:
        return {
            "dataset": "nyc-taxi",
            "month": "2024-01",
            "columns": ["PULocationID", "trip_distance"],
        }

    def query_taxi_data(self, analysis: str, limit: int = 5) -> dict[str, object]:
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["JFK Airport", 1500]],
            "row_count": 1,
            "execution_duration_ms": 12,
            "query_id": "trace-query-123",
            "truncated": False,
        }


def _tracer_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test.orchestration"), exporter


def test_executor_call_preserves_current_trace_context() -> None:
    tracer, _exporter = _tracer_and_exporter()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        voice_settings=VoiceSettings(enabled=False),
        tracer=tracer,
    )

    with tracer.start_as_current_span("ai.run") as parent:
        observed_trace_id = loop._run_with_cancellation(
            lambda: trace.get_current_span().get_span_context().trace_id,
            run_id="run_trace_context",
        )

    assert observed_trace_id == parent.get_span_context().trace_id


def test_run_emits_one_safe_ai_run_span() -> None:
    prompt = "DISTINCTIVE PRIVATE PROMPT 91827"
    tracer, exporter = _tracer_and_exporter()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        voice_settings=VoiceSettings(enabled=False),
        tracer=tracer,
    )

    result = loop.run(prompt)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "ai.run"
    assert span.parent is None
    assert span.attributes == {
        "ai.run_id": result.run_id,
        "ai.conversation_id": result.conversation_id,
        "ai.turn_type": "text",
        "ai.status": "completed",
        "gen_ai.request.model": DEFAULT_MODEL_ID,
    }
    serialized_attributes = repr(span.attributes)
    assert prompt not in serialized_attributes
    assert result.answer not in serialized_attributes


def test_run_records_exception_and_preserves_original_failure() -> None:
    class FailingLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: Mapping[str, object]
        ) -> ToolProposalResult:
            raise LLMProviderError(retryable=True)

    tracer, exporter = _tracer_and_exporter()
    loop = OrchestrationLoop(
        llm_client=FailingLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        voice_settings=VoiceSettings(enabled=False),
        tracer=tracer,
    )

    with pytest.raises(ValueError):
        loop.run("fail safely")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "ai.run"
    assert span.status.status_code is StatusCode.ERROR
    assert [event.name for event in span.events] == ["exception"]

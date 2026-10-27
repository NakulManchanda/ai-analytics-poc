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
        "session.id": result.conversation_id,
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


def _make_loop(tracer, tmp_path=None, llm=None):
    from app.capture.recorder import CaptureRecorder, CaptureSettings

    capture = (
        CaptureRecorder(CaptureSettings(directory=tmp_path))
        if tmp_path is not None
        else None
    )
    return OrchestrationLoop(
        llm_client=llm or LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        voice_settings=VoiceSettings(enabled=False),
        tracer=tracer,
        capture=capture,
    )


def _capture_records(tmp_path):
    import json

    records = []
    for path in tmp_path.rglob("*.jsonl"):
        records += [json.loads(line) for line in path.read_text().splitlines() if line]
    return records


def test_async_path_capture_trace_id_matches_ai_run_span(tmp_path) -> None:
    tracer, exporter = _tracer_and_exporter()
    loop = _make_loop(tracer, tmp_path)

    # /api/runs path: prepare_run, then execute in a separate thread with no ambient span.
    submission = loop.prepare_run("Which pickup zones have the most trips?")
    import threading

    thread = threading.Thread(target=loop.execute, args=(submission,))
    thread.start()
    thread.join()

    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["ai.run"]
    expected = format(spans[0].get_span_context().trace_id, "032x")
    assert spans[0].attributes["ai.run_id"] == submission.run_id
    assert spans[0].attributes["ai.conversation_id"] == submission.conversation_id
    assert spans[0].attributes["session.id"] == submission.conversation_id
    assert spans[0].attributes["ai.status"] == "completed"
    records = _capture_records(tmp_path)
    assert records
    assert {r["trace_id"] for r in records} == {expected}


def test_llm_call_sees_run_trace_context_and_traceparent(tmp_path) -> None:
    from opentelemetry import propagate

    seen: dict[str, object] = {}

    def record() -> None:
        seen["trace_id"] = trace.get_current_span().get_span_context().trace_id
        headers: dict[str, str] = {}
        propagate.inject(headers)
        seen["traceparent"] = headers.get("traceparent")

    class RecordingLLM(LocalFakeLLMClient):
        def propose_dataset_profile(self, *args, **kwargs):  # type: ignore[override]
            record()
            return super().propose_dataset_profile(*args, **kwargs)

        def propose_taxi_query(self, *args, **kwargs):  # type: ignore[override]
            record()
            return super().propose_taxi_query(*args, **kwargs)

        def ask(self, *args, **kwargs):  # type: ignore[override]
            record()
            return super().ask(*args, **kwargs)

        def answer_with_query_result(self, *args, **kwargs):  # type: ignore[override]
            record()
            return super().answer_with_query_result(*args, **kwargs)

    tracer, exporter = _tracer_and_exporter()
    loop = _make_loop(tracer, tmp_path, RecordingLLM())
    submission = loop.prepare_run("Which pickup zones have the most trips?")
    loop.execute(submission)

    span = exporter.get_finished_spans()[0]
    trace_id = span.get_span_context().trace_id
    assert seen["trace_id"] == trace_id
    assert str(seen["traceparent"]).split("-")[1] == format(trace_id, "032x")


def test_noop_tracer_run_completes_with_null_trace_id(tmp_path) -> None:
    loop = _make_loop(trace.NoOpTracer(), tmp_path)

    result = loop.run("Which pickup zones have the most trips?")

    assert result.status == "completed"
    records = _capture_records(tmp_path)
    assert records and {r["trace_id"] for r in records} == {None}


def test_tracer_failure_is_fail_open(tmp_path) -> None:
    class BrokenTracer:
        def start_as_current_span(self, *args, **kwargs):
            raise RuntimeError("tracer down")

    loop = _make_loop(BrokenTracer(), tmp_path)  # type: ignore[arg-type]

    result = loop.run("Which pickup zones have the most trips?")

    assert result.status == "completed"
    assert {r["trace_id"] for r in _capture_records(tmp_path)} == {None}


def test_eval_run_id_is_a_span_attribute_and_in_capture_alongside_trace_id(
    tmp_path,
) -> None:
    import threading

    tracer, exporter = _tracer_and_exporter()
    loop = _make_loop(tracer, tmp_path)

    submission = loop.prepare_run(
        "Which pickup zones have the most trips?", eval_run_id="batch-1"
    )
    thread = threading.Thread(target=loop.execute, args=(submission,))
    thread.start()
    thread.join()

    (span,) = exporter.get_finished_spans()
    assert span.attributes["ai.eval_run_id"] == "batch-1"
    records = _capture_records(tmp_path)
    assert records
    assert {r["eval_run_id"] for r in records} == {"batch-1"}
    assert {r["trace_id"] for r in records} == {
        format(span.get_span_context().trace_id, "032x")
    }


def test_span_has_no_eval_run_id_attribute_by_default(tmp_path) -> None:
    tracer, exporter = _tracer_and_exporter()
    loop = _make_loop(tracer, tmp_path)
    loop.run("Which pickup zones have the most trips?")
    (span,) = exporter.get_finished_spans()
    assert "ai.eval_run_id" not in span.attributes

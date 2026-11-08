"""Phoenix/OTEL parity for the opt-in Deep Agents strategy (#226): span attributes,
llm-call and plan events, trace-context propagation and observable trace health."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from app.llm import LLMProviderError, LocalFakeLLMClient
from app.orchestration.deep_agents_strategy import build_allowlist_middleware
from app.orchestration.loop import OrchestrationError, OrchestrationLoop
from app.state.repository import InMemoryStateRepository
from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from tests.test_deep_agents_run import (
    ARGS,
    FakeMCP,
    Publisher,
    _ai,
    _setup,
    _setup_http,
    _tool,
)
from tests.test_orchestration_loop import FakeMCPClient


def _tracer() -> tuple[Any, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("t"), exporter


def _run_span(exporter: InMemorySpanExporter) -> Any:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "ai.run"]
    return span


class TraceFakeMCP(FakeMCP):
    """Records the trace headers the MCP client would send, from inside the tool call."""

    def __init__(self) -> None:
        super().__init__()
        self.headers: list[dict[str, str]] = []

    def aggregate_taxi_data(self, **kw: Any) -> Any:
        carrier: dict[str, str] = {}
        if trace.get_current_span().get_span_context().is_valid:
            propagate.inject(carrier)
        self.headers.append(carrier)
        return super().aggregate_taxi_data(**kw)


def _trace_id(span: Any) -> str:
    return format(span.context.trace_id, "032x")


def test_root_attributes_and_llm_call_events(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, _, _ = _setup(
        monkeypatch, [_tool("aggregate_taxi_data", ARGS), _ai("There were 2 trips.")]
    )
    loop._tracer, exporter = _tracer()
    result = loop.execute(loop.prepare_run("q"))

    span = _run_span(exporter)
    assert span.attributes["agent.strategy"] == "deep_agents"
    assert span.attributes["agent.framework"] == "deepagents"
    assert span.attributes["agent.step_count"] == len(result.steps) > 0
    events = [e for e in span.events if e.name == "ai.llm_call"]
    ids = [e.attributes["ai.llm_call_id"] for e in events]
    assert len(result.llm_calls) == 2
    assert sorted(ids) == sorted(c.llm_call_id for c in result.llm_calls)
    assert len(set(ids)) == len(ids)


def test_plan_updated_event_has_counts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    todos = [
        {"content": "SECRET-TODO-A", "status": "completed"},
        {"content": "SECRET-TODO-B", "status": "in_progress"},
        {"content": "SECRET-TODO-C", "status": "pending"},
        {"content": "SECRET-TODO-D", "status": "pending"},
    ]
    loop, _, _ = _setup(
        monkeypatch, [_tool("write_todos", {"todos": todos}), _ai("done")]
    )
    loop._tracer, exporter = _tracer()
    assert loop.execute(loop.prepare_run("q")).status == "completed"

    (event,) = [e for e in _run_span(exporter).events if e.name == "ai.plan.updated"]
    assert dict(event.attributes) == {
        "todos_total": 4,
        "pending": 2,
        "in_progress": 1,
        "completed": 1,
    }
    assert "SECRET-TODO" not in repr(exporter.get_finished_spans()[0].events)


def test_no_plan_event_without_write_todos(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, _, _ = _setup(monkeypatch, [_ai("done")])
    loop._tracer, exporter = _tracer()
    loop.execute(loop.prepare_run("q"))
    assert not [e for e in _run_span(exporter).events if e.name == "ai.plan.updated"]


def test_proxy_request_and_tool_call_carry_the_run_trace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["traceparent"])
        return httpx.Response(200)

    mcp = TraceFakeMCP()
    loop, _, _, _, _ = _setup_http(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("done")],
        handler,
        mcp=mcp,
    )
    loop._tracer, exporter = _tracer()
    result = loop.execute(loop.prepare_run("q"))

    assert result.status == "completed"
    trace_id = _trace_id(_run_span(exporter))
    assert len(seen) == 2
    assert all(tp.split("-")[1] == trace_id for tp in seen)
    (carrier,) = mcp.headers
    assert carrier["traceparent"].split("-")[1] == trace_id


def test_run_metadata_records_valid_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop, repo, _ = _setup(monkeypatch, [_ai("done")])
    loop._tracer, exporter = _tracer()
    result = loop.execute(loop.prepare_run("q"))
    meta = repo.get_run(result.run_id).metadata["trace"]
    assert meta == {
        "context_valid": True,
        "trace_id": _trace_id(_run_span(exporter)),
        "propagation_failures": 0,
    }


def test_run_completes_and_flags_missing_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = TraceFakeMCP()
    loop, repo, _ = _setup(
        monkeypatch, [_tool("aggregate_taxi_data", ARGS), _ai("done")], mcp=mcp
    )
    loop._tracer = trace.NoOpTracer()
    result = loop.execute(loop.prepare_run("q"))

    assert result.status == "completed"
    assert repo.get_run(result.run_id).metadata["trace"] == {
        "context_valid": False,
        "trace_id": None,
        "propagation_failures": 0,
    }
    assert mcp.headers == [{}]


def test_propagation_failures_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(carrier: dict[str, str]) -> None:
        raise RuntimeError("inject failed")

    monkeypatch.setattr("app.orchestration.deep_agents_run.propagate.inject", boom)
    loop, _, _, _, _ = _setup_http(
        monkeypatch, [_ai("done")], lambda r: httpx.Response(200)
    )
    loop._tracer, _ = _tracer()
    result = loop.execute(loop.prepare_run("q"))
    assert result.status == "completed"
    meta = loop._repo.get_run(result.run_id).metadata
    assert meta["trace"]["propagation_failures"] == 1
    assert meta["correlation_ok"] is False


def test_compaction_hook_reports_counts_once_per_event() -> None:
    reported: list[dict[str, int]] = []
    middleware = build_allowlist_middleware(reported.append)

    def call(state: dict[str, Any]) -> None:
        request = SimpleNamespace(state=state, tools=[])
        request.override = lambda **_: request  # type: ignore[attr-defined]
        middleware.wrap_model_call(request, lambda r: "ok")

    call({"messages": [1, 2, 3]})  # no summarization yet
    event = {"_summarization_event": {"cutoff_index": 4}, "messages": [0] * 9}
    call(event)
    call(event)  # same event seen again: not re-reported
    assert reported == [{"cutoff_index": 4, "messages_total": 9}]


def test_manual_run_span_attributes() -> None:
    tracer, exporter = _tracer()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        event_publisher=Publisher(),
        tracer=tracer,
    )
    result = loop.run("How many trips were there?")
    span = _run_span(exporter)
    assert span.attributes["agent.strategy"] == "manual"
    assert span.attributes["agent.framework"] == "bespoke"
    assert span.attributes["agent.step_count"] == len(result.steps)


def test_deep_agents_failed_run_sets_terminal_span_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = openai.APIStatusError(
        "boom",
        response=httpx.Response(
            503, request=httpx.Request("POST", "http://litellm.test")
        ),
        body=None,
    )
    loop, repo, _ = _setup(monkeypatch, [error])
    loop._tracer, exporter = _tracer()
    submission = loop.prepare_run("q")
    with pytest.raises(OrchestrationError):
        loop.execute(submission)
    span = _run_span(exporter)
    assert span.attributes["ai.status"] == "failed"
    assert span.attributes["agent.step_count"] == len(
        repo.list_run_steps(submission.run_id)
    )
    assert span.attributes["agent.strategy"] == "deep_agents"


def test_manual_failed_run_sets_terminal_span_attributes() -> None:
    class NoToolCallLLMClient(LocalFakeLLMClient):
        def propose_taxi_query(self, prompt: str, schema: Any) -> Any:
            raise LLMProviderError(retryable=False, code="no_tool_call")

    tracer, exporter = _tracer()
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=NoToolCallLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=Publisher(),
        tracer=tracer,
    )
    submission = loop.prepare_run("q")
    with pytest.raises(ValueError):
        loop.execute(submission)
    span = _run_span(exporter)
    assert span.attributes["ai.status"] == "failed"
    assert span.attributes["agent.step_count"] == len(
        repo.list_run_steps(submission.run_id)
    )

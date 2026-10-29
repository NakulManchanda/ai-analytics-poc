"""Opt-in, bounded tool-result evidence on the duckdb.query span (#216)."""

import json

import pytest
from mcp_server.server import _run_governed_analytics_tool
from mcp_server.telemetry import record_result_preview
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROWS = [[f"zone-{i}", i] for i in range(40)]
RESULT = {
    "columns": ["zone", "n"],
    "rows": ROWS,
    "row_count": len(ROWS),
    "truncated": False,
}


def traced_runtime():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, provider.get_tracer("test"), exporter


def run_tool(monkeypatch, result=RESULT, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    provider, tracer, exporter = traced_runtime()
    try:
        returned = _run_governed_analytics_tool(
            tracer=tracer, tool_name="aggregate_taxi_data", runner=lambda: result
        )
    finally:
        provider.shutdown()
    (span,) = exporter.get_finished_spans()
    return returned, dict(span.attributes)


def test_result_rows_are_not_on_the_span_by_default(monkeypatch):
    monkeypatch.delenv("MCP_TRACE_RESULTS", raising=False)
    returned, attributes = run_tool(monkeypatch)
    assert returned == RESULT
    assert "ai.result.preview" not in attributes
    assert attributes["ai.row_count"] == len(ROWS)  # existing metadata unchanged


def test_opt_in_attaches_a_bounded_preview_with_truncation_markers(monkeypatch):
    returned, attributes = run_tool(
        monkeypatch, MCP_TRACE_RESULTS="1", MCP_TRACE_RESULT_MAX_BYTES="256"
    )
    assert returned == RESULT  # the tool result itself is never trimmed
    preview = attributes["ai.result.preview"]
    assert len(preview.encode()) <= 256
    shown = json.loads(preview)
    assert shown["columns"] == ["zone", "n"]
    assert shown["rows"] == ROWS[: len(shown["rows"])]  # whole leading rows only
    assert attributes["ai.result.preview_rows"] == len(shown["rows"]) < len(ROWS)
    assert attributes["ai.result.preview_truncated"] is True


def test_small_result_is_previewed_in_full(monkeypatch):
    small = {"columns": ["n"], "rows": [[1], [2]], "row_count": 2, "truncated": False}
    _, attributes = run_tool(monkeypatch, result=small, MCP_TRACE_RESULTS="true")
    assert json.loads(attributes["ai.result.preview"])["rows"] == [[1], [2]]
    assert attributes["ai.result.preview_rows"] == 2
    assert attributes["ai.result.preview_truncated"] is False


@pytest.mark.parametrize("bad", ["not-a-number", "-5", "0"])
def test_invalid_byte_limit_falls_back_or_clamps(monkeypatch, bad):
    _, attributes = run_tool(
        monkeypatch, MCP_TRACE_RESULTS="1", MCP_TRACE_RESULT_MAX_BYTES=bad
    )
    assert len(attributes["ai.result.preview"].encode()) <= 4096


def test_results_without_rows_are_skipped_and_never_raise(monkeypatch):
    monkeypatch.setenv("MCP_TRACE_RESULTS", "1")
    provider, tracer, exporter = traced_runtime()
    try:
        with tracer.start_as_current_span("x") as span:
            record_result_preview(span, {"error": "validation"})
            record_result_preview(span, None)
            record_result_preview(span, {"rows": object()})
    finally:
        provider.shutdown()
    assert "ai.result.preview" not in dict(exporter.get_finished_spans()[0].attributes)

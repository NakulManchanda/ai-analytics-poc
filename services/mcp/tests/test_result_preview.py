"""Opt-in, bounded tool-result evidence on the duckdb.query span (#216)."""

import json
import time

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


def preview_of(monkeypatch, result, limit):
    monkeypatch.setenv("MCP_TRACE_RESULTS", "1")
    monkeypatch.setenv("MCP_TRACE_RESULT_MAX_BYTES", str(limit))
    provider, tracer, exporter = traced_runtime()
    try:
        with tracer.start_as_current_span("x") as span:
            record_result_preview(span, result)
    finally:
        provider.shutdown()
    return dict(exporter.get_finished_spans()[0].attributes)


@pytest.mark.parametrize("flag", [None, "0", "false", ""])
def test_flag_off_or_unset_exports_nothing(monkeypatch, flag):
    monkeypatch.delenv("MCP_TRACE_RESULTS", raising=False)
    if flag is not None:
        monkeypatch.setenv("MCP_TRACE_RESULTS", flag)
    provider, tracer, exporter = traced_runtime()
    try:
        with tracer.start_as_current_span("x") as span:
            record_result_preview(span, RESULT)
    finally:
        provider.shutdown()
    attributes = dict(exporter.get_finished_spans()[0].attributes)
    assert not any(key.startswith("ai.result.") for key in attributes)


def test_oversized_columns_omit_the_preview(monkeypatch):
    result = {"columns": ["c" * 1000], "rows": [[1], [2]]}
    attributes = preview_of(monkeypatch, result, 256)
    assert "ai.result.preview" not in attributes
    assert attributes["ai.result.preview_omitted"] is True
    assert attributes["ai.result.preview_rows"] == 0
    assert attributes["ai.result.preview_truncated"] is True


def test_columns_that_fit_with_zero_rows_still_export_a_header(monkeypatch):
    result = {"columns": ["c" * 100], "rows": [["x" * 500]]}
    attributes = preview_of(monkeypatch, result, 256)
    assert json.loads(attributes["ai.result.preview"]) == {
        "columns": ["c" * 100],
        "rows": [],
    }
    assert attributes["ai.result.preview_rows"] == 0
    assert attributes["ai.result.preview_truncated"] is True
    assert "ai.result.preview_omitted" not in attributes


def test_cap_counts_utf8_bytes_not_characters(monkeypatch):
    rows = [["\u00e9\u4e2d\U0001f600" * 10] for _ in range(50)]
    result = {"columns": ["t"], "rows": rows}
    attributes = preview_of(monkeypatch, result, 300)
    preview = attributes["ai.result.preview"]
    assert len(preview) < 300 < len(preview.encode("utf-8")) + 120  # multibyte present
    assert len(preview.encode("utf-8")) <= 300
    assert 0 < attributes["ai.result.preview_rows"] < 50


@pytest.mark.parametrize("limit", [-1, 0, 1, 10, 255, 256, 257, 400])
def test_every_exported_preview_respects_the_limit(monkeypatch, limit):
    from mcp_server.telemetry import _RESULT_PREVIEW_MIN_BYTES

    effective = max(_RESULT_PREVIEW_MIN_BYTES, limit)
    attributes = preview_of(monkeypatch, RESULT, limit)
    assert len(attributes["ai.result.preview"].encode("utf-8")) <= effective


def test_large_result_matches_single_dumps_and_whole_row_prefix(monkeypatch):
    rows = [[f"zone-{i}", i, None, 1.5, "\u00e9"] for i in range(5000)]
    result = {"columns": ["a", "b", "c", "d", "e"], "rows": rows}
    attributes = preview_of(monkeypatch, result, 2000)
    n = attributes["ai.result.preview_rows"]
    expected = json.dumps(
        {"columns": result["columns"], "rows": rows[:n]},
        separators=(",", ":"),
        default=str,
    )
    assert attributes["ai.result.preview"] == expected
    assert len(expected.encode("utf-8")) <= 2000
    # Greedy and maximal: one more row would not have fit.
    bigger = json.dumps(
        {"columns": result["columns"], "rows": rows[: n + 1]},
        separators=(",", ":"),
        default=str,
    )
    assert len(bigger.encode("utf-8")) > 2000
    assert attributes["ai.result.preview_truncated"] is True


def test_preview_cost_is_small_for_thousands_of_rows(monkeypatch, capsys):
    rows = [[f"zone-{i}", i] for i in range(5000)]
    result = {"columns": ["zone", "n"], "rows": rows}
    start = time.perf_counter()
    for _ in range(20):
        preview_of(monkeypatch, result, 4096)
    per_call_ms = (time.perf_counter() - start) / 20 * 1000
    print(f"preview on 5000 rows: {per_call_ms:.2f} ms/call")
    assert per_call_ms < 250  # very loose; guards against quadratic behaviour only

import pytest
from app import mcp_client
from app.mcp_client import FastMCPDatasetProfileClient, MCPToolError
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


@pytest.fixture
def tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test.mcp_client")


def _capture_client_construction(monkeypatch):
    transports = []

    class CapturingTransport:
        def __init__(self, url, *, headers):
            self.url = url
            self.headers = headers
            transports.append(self)

    class CapturingClient:
        def __init__(self, transport):
            self.transport = transport

    monkeypatch.setattr(
        mcp_client, "StreamableHttpTransport", CapturingTransport, raising=False
    )
    monkeypatch.setattr(mcp_client, "Client", CapturingClient)
    return transports


def test_client_injects_active_w3c_trace_context(monkeypatch, tracer) -> None:
    transports = _capture_client_construction(monkeypatch)
    client = FastMCPDatasetProfileClient("http://mcp.example/mcp")

    with tracer.start_as_current_span("parent") as parent:
        client._client()

    headers = transports[0].headers
    assert headers["traceparent"].split("-")[1] == format(
        parent.get_span_context().trace_id, "032x"
    )
    assert set(headers) <= {"traceparent", "tracestate"}


def test_client_uses_no_trace_headers_without_an_active_recording_span(
    monkeypatch,
) -> None:
    transports = _capture_client_construction(monkeypatch)

    FastMCPDatasetProfileClient()._client()

    assert transports[0].headers == {}


def test_query_rejects_disallowed_analysis_before_building_transport(
    monkeypatch,
) -> None:
    transports = _capture_client_construction(monkeypatch)

    with pytest.raises(MCPToolError) as exc_info:
        FastMCPDatasetProfileClient().query_taxi_data(analysis="arbitrary_sql", limit=1)

    assert exc_info.value.retryable is False
    assert transports == []

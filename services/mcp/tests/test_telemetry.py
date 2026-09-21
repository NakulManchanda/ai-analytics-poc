import asyncio
import importlib
import json
import sys

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, StatusCode, TraceFlags


def make_remote_parent_context() -> SpanContext:
    return SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


def make_traceparent(context: SpanContext) -> str:
    return f"00-{context.trace_id:032x}-{context.span_id:016x}-01"


def build_test_mcp(*, tracer: trace.Tracer):
    from fastmcp import FastMCP
    from mcp_server.telemetry import MCPTracingMiddleware

    server = FastMCP("telemetry-test")
    server.add_middleware(MCPTracingMiddleware(tracer))
    return server


async def call_mcp_asgi(app, *, headers: list[tuple[bytes, bytes]] | None = None):
    request_headers = {
        key.decode(): value.decode()
        for key, value in [
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            *(headers or []),
        ]
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post(
                "/mcp",
                headers=request_headers,
                content=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "telemetry-test",
                                "version": "1.0",
                            },
                        },
                    }
                ),
            )


def traced_runtime():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, provider.get_tracer("test"), exporter


def test_asgi_entrypoint_propagates_remote_trace_to_governed_tool(monkeypatch):
    """The served HTTP app must keep the incoming trace through the tool query."""
    from dataset_spike.analytics import DatasetProfile
    from mcp_server import server, telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create({"service.name": "analytics-mcp"})
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    runtime = telemetry.MCPTracingRuntime(tracer=tracer, provider=provider)
    profile = DatasetProfile(1, 1, ["pickup_zone"], [], {}, 1, 1)
    original_build_mcp = server.build_mcp

    def query_runner(*, analysis: str, limit: int) -> dict[str, object]:
        assert (analysis, limit) == ("top_pickup_zones", 2)
        return {
            "columns": ["pickup_zone"],
            "rows": [["Alpha"]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "asgi_trace",
            "truncated": False,
        }

    def build_test_entrypoint_mcp(*, tracer):
        assert tracer is runtime.tracer
        return original_build_mcp(
            profile_loader=lambda: profile,
            query_runner=query_runner,
            tracer=tracer,
        )

    monkeypatch.setattr(telemetry, "build_mcp_tracing", lambda _settings: runtime)
    monkeypatch.setattr(server, "build_mcp", build_test_entrypoint_mcp)
    sys.modules.pop("mcp_server.asgi", None)
    asgi = importlib.import_module("mcp_server.asgi")
    parent_context = make_remote_parent_context()

    async def exercise_protocol():
        async with asgi.app.router.lifespan_context(asgi.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=asgi.app),
                base_url="http://testserver",
            ) as client:
                headers = {
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                    "traceparent": make_traceparent(parent_context),
                }
                initialized = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "telemetry-test", "version": "1.0"},
                        },
                    },
                )
                session_id = initialized.headers["mcp-session-id"]
                await client.post(
                    "/mcp",
                    headers={**headers, "mcp-session-id": session_id},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                return await client.post(
                    "/mcp",
                    headers={**headers, "mcp-session-id": session_id},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "query_taxi_data",
                            "arguments": {"analysis": "top_pickup_zones", "limit": 2},
                        },
                    },
                )

    try:
        response = asyncio.run(exercise_protocol())
    finally:
        provider.shutdown()
        sys.modules.pop("mcp_server.asgi", None)

    assert response.is_success
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "mcp.request",
        "mcp.tool.execute",
        "duckdb.query",
    }
    assert {span.context.trace_id for span in spans} == {parent_context.trace_id}
    by_name = {span.name: span for span in spans}
    assert by_name["mcp.request"].parent.span_id == parent_context.span_id
    assert (
        by_name["mcp.tool.execute"].parent.span_id
        == by_name["mcp.request"].context.span_id
    )
    assert (
        by_name["duckdb.query"].parent.span_id
        == by_name["mcp.tool.execute"].context.span_id
    )
    assert by_name["mcp.request"].resource.attributes["service.name"] == "analytics-mcp"
    assert (
        by_name["mcp.request"].resource.attributes["service.name"] != "ai-analytics-app"
    )


def test_asgi_entrypoint_fails_open_without_tracing_endpoint(monkeypatch):
    """A disabled traced entrypoint remains a usable HTTP MCP server without exporters."""
    from dataset_spike.analytics import DatasetProfile
    from mcp_server import server, telemetry

    profile = DatasetProfile(1, 1, ["pickup_zone"], [], {}, 1, 1)
    original_build_mcp = server.build_mcp
    monkeypatch.delenv("OTEL_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setattr(
        telemetry,
        "OTLPSpanExporter",
        lambda **_kwargs: pytest.fail(
            "disabled tracing must not construct an exporter"
        ),
    )
    monkeypatch.setattr(
        server,
        "build_mcp",
        lambda *, tracer: original_build_mcp(
            profile_loader=lambda: profile,
            tracer=tracer,
        ),
    )
    sys.modules.pop("mcp_server.asgi", None)
    asgi = importlib.import_module("mcp_server.asgi")
    try:
        response = asyncio.run(call_mcp_asgi(asgi.app))
    finally:
        sys.modules.pop("mcp_server.asgi", None)

    assert response.is_success
    assert asgi.tracing_runtime.provider is None


def test_mcp_telemetry_is_disabled_by_default(monkeypatch):
    from mcp_server.telemetry import MCPTelemetrySettings, build_mcp_tracing

    monkeypatch.delenv("OTEL_TRACING_ENABLED", raising=False)
    assert build_mcp_tracing(MCPTelemetrySettings.from_environment()).provider is None


def test_enabled_mcp_telemetry_without_endpoint_fails_open(monkeypatch, caplog):
    from mcp_server.telemetry import MCPTelemetrySettings, build_mcp_tracing

    monkeypatch.setenv("OTEL_TRACING_ENABLED", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    runtime = build_mcp_tracing(MCPTelemetrySettings.from_environment())
    assert runtime.provider is None
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in caplog.text


def test_enabled_mcp_telemetry_resource_has_only_service_and_environment():
    from mcp_server.telemetry import MCPTelemetrySettings, build_mcp_tracing

    exporter = InMemorySpanExporter()
    runtime = build_mcp_tracing(
        MCPTelemetrySettings(
            enabled=True,
            service_name="telemetry-test",
            deployment_environment="test",
            traces_endpoint="http://collector.test/v1/traces",
        ),
        span_processor=SimpleSpanProcessor(exporter),
    )
    assert runtime.provider is not None
    try:
        assert dict(runtime.provider.resource.attributes) == {
            "service.name": "telemetry-test",
            "deployment.environment.name": "test",
        }
    finally:
        runtime.provider.shutdown()


def test_fastmcp_middleware_extracts_traceparent_and_makes_server_child_current():
    from fastmcp.server.middleware import Middleware

    current_spans = []

    class CurrentSpanMiddleware(Middleware):
        async def on_request(self, context, call_next):
            current_spans.append(trace.get_current_span().get_span_context())
            return await call_next(context)

    parent_context = make_remote_parent_context()
    provider, tracer, exporter = traced_runtime()
    server = build_test_mcp(tracer=tracer)
    server.add_middleware(CurrentSpanMiddleware())
    try:
        response = asyncio.run(
            call_mcp_asgi(
                server.http_app(path="/mcp"),
                headers=[(b"traceparent", make_traceparent(parent_context).encode())],
            )
        )
    finally:
        provider.shutdown()

    spans = exporter.get_finished_spans()
    assert response.is_success
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "mcp.request"
    assert span.parent.span_id == parent_context.span_id
    assert span.context.trace_id == parent_context.trace_id
    assert span.attributes == {"rpc.system": "mcp", "rpc.method": "initialize"}
    assert current_spans == [span.context]


def test_fastmcp_middleware_creates_root_span_without_valid_traceparent():
    provider, tracer, exporter = traced_runtime()
    try:
        response = asyncio.run(
            call_mcp_asgi(build_test_mcp(tracer=tracer).http_app(path="/mcp"))
        )
    finally:
        provider.shutdown()

    spans = exporter.get_finished_spans()
    assert response.is_success
    assert len(spans) == 1
    assert spans[0].context.is_valid
    assert spans[0].parent is None


def test_fastmcp_middleware_ignores_malformed_traceparent():
    provider, tracer, exporter = traced_runtime()
    try:
        response = asyncio.run(
            call_mcp_asgi(
                build_test_mcp(tracer=tracer).http_app(path="/mcp"),
                headers=[(b"traceparent", b"00-not-a-traceparent")],
            )
        )
    finally:
        provider.shutdown()

    assert response.is_success
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].context.is_valid
    assert spans[0].parent is None


def test_fastmcp_middleware_creates_root_span_when_http_request_is_unavailable():
    from mcp_server import telemetry

    async def downstream(_context):
        return "downstream response"

    provider, tracer, exporter = traced_runtime()
    middleware = telemetry.MCPTracingMiddleware(tracer)

    try:
        response = asyncio.run(
            middleware.on_request(context=object(), call_next=downstream)
        )
    finally:
        provider.shutdown()

    spans = exporter.get_finished_spans()
    assert response == "downstream response"
    assert len(spans) == 1
    assert spans[0].context.is_valid
    assert spans[0].parent is None


def test_fastmcp_middleware_marks_downstream_exception_without_exporting_details(
    monkeypatch,
):
    from mcp_server import telemetry
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "headers": [(b"x-sensitive", b"do-not-export")],
    }
    monkeypatch.setattr(telemetry, "get_http_request", lambda: Request(scope))

    sentinel = "SELECT private_prompt FROM secrets WHERE user_token=abc"

    async def downstream(_context):
        raise RuntimeError(sentinel)

    provider, tracer, exporter = traced_runtime()
    middleware = telemetry.MCPTracingMiddleware(tracer)

    try:
        with pytest.raises(RuntimeError, match=sentinel):
            asyncio.run(middleware.on_request(context=object(), call_next=downstream))
    finally:
        provider.shutdown()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is StatusCode.ERROR
    serialized = json.dumps(
        {
            "attributes": dict(spans[0].attributes),
            "events": [
                {"name": event.name, "attributes": dict(event.attributes)}
                for event in spans[0].events
            ],
        }
    )
    assert "x-sensitive" not in serialized
    assert "do-not-export" not in serialized
    assert sentinel not in serialized

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

LOGGER = logging.getLogger(__name__)
INSTRUMENTATION_SCOPE = "ai_analytics_poc.mcp"


def _environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MCPTelemetrySettings:
    """Small, explicit configuration surface for MCP tracing."""

    enabled: bool = False
    service_name: str = "analytics-mcp"
    deployment_environment: str = "local"
    traces_endpoint: str | None = None

    @classmethod
    def from_environment(cls) -> MCPTelemetrySettings:
        return cls(
            enabled=_environment_flag("OTEL_TRACING_ENABLED"),
            service_name=os.getenv("OTEL_SERVICE_NAME", "analytics-mcp"),
            deployment_environment=os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "local"),
            traces_endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
        )


@dataclass(frozen=True)
class MCPTracingRuntime:
    tracer: trace.Tracer
    provider: TracerProvider | None


def _noop_runtime() -> MCPTracingRuntime:
    return MCPTracingRuntime(
        tracer=trace.get_tracer(INSTRUMENTATION_SCOPE),
        provider=None,
    )


def build_mcp_tracing(
    settings: MCPTelemetrySettings,
    *,
    span_processor: SpanProcessor | None = None,
) -> MCPTracingRuntime:
    """Build an isolated provider, falling back safely when tracing is unavailable."""

    if not settings.enabled:
        return _noop_runtime()
    if not settings.traces_endpoint:
        LOGGER.warning(
            "Tracing is enabled but OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is unset; "
            "continuing without trace export"
        )
        return _noop_runtime()

    try:
        provider = TracerProvider(
            resource=Resource(
                {
                    "service.name": settings.service_name,
                    "deployment.environment.name": settings.deployment_environment,
                }
            )
        )
        processor = span_processor or BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.traces_endpoint)
        )
        provider.add_span_processor(processor)
        return MCPTracingRuntime(
            tracer=provider.get_tracer(INSTRUMENTATION_SCOPE),
            provider=provider,
        )
    except Exception:
        LOGGER.exception(
            "Could not initialize OpenTelemetry; continuing without tracing"
        )
        return _noop_runtime()


class MCPTracingMiddleware(Middleware):
    """Create a privacy-safe server span for each incoming MCP request."""

    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    async def on_request(self, context, call_next):
        try:
            request = get_http_request()
        except RuntimeError as error:
            if str(error) != "No active HTTP request found.":
                raise
            request = None
        parent = propagate.extract(dict(request.headers) if request else {})
        with self._tracer.start_as_current_span(
            "mcp.request", context=parent, kind=trace.SpanKind.SERVER
        ) as span:
            span.set_attributes({"rpc.system": "mcp", "rpc.method": "unknown"})
            return await call_next(context)

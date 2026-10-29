from __future__ import annotations

import functools
import json
import logging
import os
from dataclasses import dataclass

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor
from opentelemetry.trace import Status, StatusCode

LOGGER = logging.getLogger(__name__)
INSTRUMENTATION_SCOPE = "ai_analytics_poc.mcp"
SCHEMA_RESOURCE_URI = "dataset://nyc-taxi/schema"
_SAFE_PROTOCOL_METHODS = {
    "tools/call",
    "resources/read",
    "initialize",
    "tools/list",
    "resources/list",
}


# Discovery/keep-alive requests (UI status polling and client handshakes). With no caller trace they
# would each become a one-span trace in the backend, so they are not traced unless they join one.
_DISCOVERY_METHODS = frozenset(
    {
        "initialize",
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "prompts/list",
        "ping",
    }
)

_RESULT_PREVIEW_DEFAULT_BYTES = 4096
_RESULT_PREVIEW_MIN_BYTES = 256
_RESULT_PREVIEW_MAX_BYTES = 65536


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
        parent = propagate.extract(get_http_headers())
        method = getattr(context, "method", None)
        if (
            method in _DISCOVERY_METHODS
            and not trace.get_current_span(parent).get_span_context().is_valid
        ):
            return await call_next(context)
        with self._tracer.start_as_current_span(
            "mcp.request",
            context=parent,
            kind=trace.SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            method = method if method in _SAFE_PROTOCOL_METHODS else "unknown"
            span.set_attributes({"rpc.system": "mcp", "rpc.method": method})
            try:
                return await call_next(context)
            except Exception:
                span.set_status(Status(StatusCode.ERROR))
                raise

    async def on_call_tool(self, context, call_next):
        with self._tracer.start_as_current_span(
            "mcp.tool.execute",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("mcp.tool.name", context.message.name)
            try:
                return await call_next(context)
            except Exception:
                span.set_status(Status(StatusCode.ERROR))
                raise

    async def on_read_resource(self, context, call_next):
        with self._tracer.start_as_current_span(
            "mcp.resource.read",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("mcp.resource.uri", SCHEMA_RESOURCE_URI)
            try:
                return await call_next(context)
            except Exception:
                span.set_status(Status(StatusCode.ERROR))
                raise


def record_result_preview(span: trace.Span, result: object) -> None:
    """Opt-in (MCP_TRACE_RESULTS=1): attach a bounded copy of a tool's result rows to its span.

    Off by default because rows are data, not metadata. The preview is capped at
    MCP_TRACE_RESULT_MAX_BYTES (default 4096, clamped to 256..65536) by dropping trailing whole
    rows; `ai.result.preview_rows` and `ai.result.preview_truncated` say what was kept.
    Never raises.
    """
    if not _environment_flag("MCP_TRACE_RESULTS"):
        return
    try:
        rows = result.get("rows") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            return
        try:
            limit = int(
                os.getenv("MCP_TRACE_RESULT_MAX_BYTES", _RESULT_PREVIEW_DEFAULT_BYTES)
            )
        except ValueError:
            limit = _RESULT_PREVIEW_DEFAULT_BYTES
        limit = max(_RESULT_PREVIEW_MIN_BYTES, min(limit, _RESULT_PREVIEW_MAX_BYTES))
        dump = functools.partial(json.dumps, separators=(",", ":"), default=str)
        head = '{"columns":' + dump(result.get("columns")) + ',"rows":['
        used = len(head.encode("utf-8")) + 2  # closing "]}"
        if used > limit:
            # Even a zero-row preview would exceed the cap: export no data, only the fact.
            span.set_attributes(
                {
                    "ai.result.preview_omitted": True,
                    "ai.result.preview_rows": 0,
                    "ai.result.preview_truncated": len(rows) > 0,
                }
            )
            return
        kept: list[str] = []
        for (
            row
        ) in rows:  # one serialization per row; stop at the first that does not fit
            encoded = dump(row)
            cost = len(encoded.encode("utf-8")) + (1 if kept else 0)
            if used + cost > limit:
                break
            kept.append(encoded)
            used += cost
        span.set_attributes(
            {
                "ai.result.preview": head + ",".join(kept) + "]}",
                "ai.result.preview_rows": len(kept),
                "ai.result.preview_truncated": len(kept) < len(rows),
            }
        )
    except Exception:
        LOGGER.warning("Could not record tool result preview", exc_info=True)

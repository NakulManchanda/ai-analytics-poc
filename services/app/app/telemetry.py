from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

LOGGER = logging.getLogger(__name__)
INSTRUMENTATION_SCOPE = "ai_analytics_poc.orchestration"


def _environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelemetrySettings:
    """Small, explicit configuration surface for the O1 tracing slice."""

    enabled: bool = False
    service_name: str = "ai-analytics-app"
    deployment_environment: str = "local"
    traces_endpoint: str | None = None

    @classmethod
    def from_environment(cls) -> "TelemetrySettings":
        return cls(
            enabled=_environment_flag("OTEL_TRACING_ENABLED"),
            service_name=os.getenv("OTEL_SERVICE_NAME", "ai-analytics-app"),
            deployment_environment=os.getenv(
                "OTEL_DEPLOYMENT_ENVIRONMENT", "local"
            ),
            traces_endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
        )


@dataclass(frozen=True)
class TracingRuntime:
    tracer: trace.Tracer
    provider: TracerProvider | None


def _noop_runtime() -> TracingRuntime:
    return TracingRuntime(
        tracer=trace.get_tracer(INSTRUMENTATION_SCOPE),
        provider=None,
    )


def build_tracing(
    settings: TelemetrySettings,
    *,
    span_processor: SpanProcessor | None = None,
) -> TracingRuntime:
    """Build an isolated tracer provider, falling back safely when unavailable."""

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
            resource=Resource.create(
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
        return TracingRuntime(
            tracer=provider.get_tracer(INSTRUMENTATION_SCOPE),
            provider=provider,
        )
    except Exception:
        LOGGER.exception("Could not initialize OpenTelemetry; continuing without tracing")
        return _noop_runtime()

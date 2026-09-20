import logging

from app.telemetry import TelemetrySettings, build_tracing

_TELEMETRY_ENVIRONMENT = (
    "OTEL_TRACING_ENABLED",
    "OTEL_SERVICE_NAME",
    "OTEL_DEPLOYMENT_ENVIRONMENT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)


def _clear_telemetry_environment(monkeypatch) -> None:
    for name in _TELEMETRY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def test_telemetry_is_disabled_by_default(monkeypatch) -> None:
    _clear_telemetry_environment(monkeypatch)

    settings = TelemetrySettings.from_environment()

    assert settings == TelemetrySettings()
    runtime = build_tracing(settings)
    assert runtime.provider is None


def test_telemetry_reads_service_and_otlp_environment(monkeypatch) -> None:
    _clear_telemetry_environment(monkeypatch)
    monkeypatch.setenv("OTEL_TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "test-app")
    monkeypatch.setenv("OTEL_DEPLOYMENT_ENVIRONMENT", "test-compose")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://collector:4318/v1/traces",
    )

    settings = TelemetrySettings.from_environment()

    assert settings.enabled is True
    assert settings.service_name == "test-app"
    assert settings.deployment_environment == "test-compose"
    assert settings.traces_endpoint == "http://collector:4318/v1/traces"


def test_enabled_telemetry_without_endpoint_falls_back_to_noop(
    monkeypatch, caplog
) -> None:
    _clear_telemetry_environment(monkeypatch)
    monkeypatch.setenv("OTEL_TRACING_ENABLED", "true")

    with caplog.at_level(logging.WARNING):
        runtime = build_tracing(TelemetrySettings.from_environment())

    assert runtime.provider is None
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in caplog.text

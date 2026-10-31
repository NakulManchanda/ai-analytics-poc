import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_observability_compose_overlay_declares_all_services() -> None:
    compose_path = ROOT / "docker-compose.observability.yml"
    assert compose_path.exists()
    content = yaml.safe_load(compose_path.read_text())
    services = content.get("services", {})

    expected_services = [
        "app",
        "mcp",
        "otel-collector",
        "prometheus",
        "blackbox-exporter",
        "loki",
        "alloy",
        "grafana",
    ]
    for svc in expected_services:
        assert (
            svc in services
        ), f"Service {svc} missing from docker-compose.observability.yml"


def test_observability_configuration_files_are_valid() -> None:
    # Prometheus config
    prom_cfg = ROOT / "infra/observability/core/prometheus.yml"
    assert prom_cfg.exists()
    prom_data = yaml.safe_load(prom_cfg.read_text())
    assert "scrape_configs" in prom_data

    # Verify Blackbox probe targets match container network ports
    scrape_jobs = {job["job_name"]: job for job in prom_data["scrape_configs"]}
    assert "blackbox-http" in scrape_jobs
    http_targets = [
        target
        for group in scrape_jobs["blackbox-http"]["static_configs"]
        for target in group["targets"]
    ]
    assert "http://app:8080/health" in http_targets
    assert "http://phoenix:6006/healthz" in http_targets
    assert "http://jaeger:16686/" not in http_targets  # Jaeger is optional (#221)

    assert "blackbox-tcp" in scrape_jobs
    tcp_targets = [
        target
        for group in scrape_jobs["blackbox-tcp"]["static_configs"]
        for target in group["targets"]
    ]
    assert "mcp:8001" in tcp_targets
    assert "redis:6379" in tcp_targets
    assert "otel-collector:4318" in tcp_targets

    # Blackbox config
    blackbox_cfg = ROOT / "infra/observability/core/blackbox.yml"
    assert blackbox_cfg.exists()
    bb_data = yaml.safe_load(blackbox_cfg.read_text())
    assert "modules" in bb_data
    assert "http_2xx" in bb_data["modules"]
    assert "tcp_connect" in bb_data["modules"]

    # Loki config
    loki_cfg = ROOT / "infra/observability/core/loki.yml"
    assert loki_cfg.exists()
    loki_data = yaml.safe_load(loki_cfg.read_text())
    assert loki_data.get("auth_enabled") is False

    # Grafana datasources
    ds_cfg = (
        ROOT
        / "infra/observability/core/grafana/provisioning/datasources/datasources.yml"
    )
    assert ds_cfg.exists()
    ds_data = yaml.safe_load(ds_cfg.read_text())
    ds_names = [d["name"] for d in ds_data.get("datasources", [])]
    assert "Prometheus" in ds_names
    assert "Loki" in ds_names
    assert "Jaeger" not in ds_names  # provisioned only by the Jaeger overlay (#221)

    # Grafana dashboard JSON
    dash_json = ROOT / "infra/observability/core/grafana/dashboards/local-stack.json"
    assert dash_json.exists()
    dash_data = json.loads(dash_json.read_text())
    assert dash_data.get("title") == "AI Analytics — Local Stack"
    assert dash_data.get("uid") == "ai-analytics-local-stack"


def test_makefile_observability_targets_include_grafana_and_prometheus() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "OBSERVABILITY_GRAFANA_PORT ?= 13001" in makefile
    assert "OBSERVABILITY_PROMETHEUS_PORT ?= 19090" in makefile
    assert "Grafana:    http://127.0.0.1:$(OBSERVABILITY_GRAFANA_PORT)" in makefile
    assert "Prometheus: http://127.0.0.1:$(OBSERVABILITY_PROMETHEUS_PORT)" in makefile
    assert "observability-dev-burst:" in makefile
    assert "scripts/burst_traffic.py" in makefile

    # Verify dry-run outputs
    info_output = subprocess.check_output(
        ["make", "-n", "observability-dev-info"],
        cwd=ROOT,
        text=True,
    )
    assert "Grafana:    http://127.0.0.1:13001" in info_output
    assert "Prometheus: http://127.0.0.1:19090" in info_output


def _compose_config(*files: str) -> subprocess.CompletedProcess:
    args = ["docker", "compose"]
    for name in files:
        args += ["-f", name]
    return subprocess.run([*args, "config"], cwd=ROOT, capture_output=True, text=True)


def test_docker_compose_config_validates_successfully() -> None:
    base = ["docker-compose.yml", "docker-compose.phoenix.yml"]
    for extra in (
        ["docker-compose.observability.yml"],
        ["docker-compose.observability.yml", "docker-compose.jaeger.yml"],
    ):
        result = _compose_config(*base, *extra)
        assert result.returncode == 0, f"{extra}: {result.stderr}"


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def test_phoenix_is_the_default_trace_backend_and_jaeger_is_optional() -> None:
    observability = _load("docker-compose.observability.yml")["services"]
    assert "jaeger" not in observability
    assert "phoenix" in observability["otel-collector"]["depends_on"]
    assert "jaeger" not in observability["grafana"].get("depends_on", [])
    core = _load("infra/observability/core/otel-collector.yaml")
    assert core["service"]["pipelines"]["traces"]["exporters"] == ["otlphttp/phoenix"]
    assert core["exporters"]["otlphttp/phoenix"]["endpoint"] == "http://phoenix:6006"

    jaeger = _load("docker-compose.jaeger.yml")["services"]
    assert "jaeger" in jaeger
    assert "--config=/etc/otelcol/jaeger.yaml" in jaeger["otel-collector"]["command"]
    extra = _load("infra/observability/jaeger/otel-collector-jaeger.yaml")
    assert extra["service"]["pipelines"]["traces"]["exporters"] == [
        "otlphttp/phoenix",
        "otlp_grpc/jaeger",
    ]
    datasource = _load("infra/observability/jaeger/jaeger-datasource.yml")
    assert [d["name"] for d in datasource["datasources"]] == ["Jaeger"]


def test_phoenix_overlay_works_without_litellm_and_litellm_logger_is_separate() -> None:
    assert "litellm" not in _load("docker-compose.phoenix.yml")["services"]
    litellm = _load("docker-compose.phoenix-litellm.yml")["services"]["litellm"]
    assert "phoenix" in litellm["environment"]["PHOENIX_COLLECTOR_HTTP_ENDPOINT"]


def test_run_io_text_only_goes_where_phoenix_is_the_only_destination() -> None:
    """AI_TRACE_RUN_IO puts question/answer text on ai.run spans whatever the exporter is."""
    phoenix_env = _load("docker-compose.phoenix.yml")["services"]["app"]["environment"]
    assert phoenix_env["AI_TRACE_RUN_IO"].endswith(":-1}")
    # Default observability stack: app -> Collector -> Phoenix only, so the flag stays on.
    obs_env = _load("docker-compose.observability.yml")["services"]["app"][
        "environment"
    ]
    assert "otel-collector" in obs_env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
    assert "AI_TRACE_RUN_IO" not in obs_env
    # Once spans also go to Jaeger, the text is forced off.
    jaeger_env = _load("docker-compose.jaeger.yml")["services"]["app"]["environment"]
    assert jaeger_env["AI_TRACE_RUN_IO"] == "0"


def test_makefile_layers_overlays_in_order_and_jaeger_is_opt_in() -> None:
    lines = (ROOT / "Makefile").read_text().splitlines()
    litellm = next(x for x in lines if x.startswith("LITELLM_COMPOSE ="))
    order = [
        "docker-compose.litellm.yml",
        "docker-compose.phoenix.yml",
        "docker-compose.phoenix-litellm.yml",
        "docker-compose.observability.yml",
        "docker-compose.litellm-otel.yml",
        "docker-compose.jaeger.yml",
    ]
    assert [litellm.index(f) for f in order] == sorted(litellm.index(f) for f in order)
    obs = next(x for x in lines if x.startswith("OBS_COMPOSE ="))
    assert obs.index("docker-compose.phoenix.yml") < obs.index(
        "docker-compose.observability.yml"
    )
    assert "$(if $(filter 1,$(JAEGER)),-f docker-compose.jaeger.yml)" in obs
    assert "observability-jaeger-smoke" in (ROOT / "Makefile").read_text()

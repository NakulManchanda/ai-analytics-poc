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
        "jaeger",
        "prometheus",
        "blackbox-exporter",
        "loki",
        "alloy",
        "grafana",
    ]
    for svc in expected_services:
        assert svc in services, f"Service {svc} missing from docker-compose.observability.yml"


def test_observability_configuration_files_are_valid() -> None:
    # Prometheus config
    prom_cfg = ROOT / "observability/prometheus.yml"
    assert prom_cfg.exists()
    prom_data = yaml.safe_load(prom_cfg.read_text())
    assert "scrape_configs" in prom_data

    # Blackbox config
    blackbox_cfg = ROOT / "observability/blackbox.yml"
    assert blackbox_cfg.exists()
    bb_data = yaml.safe_load(blackbox_cfg.read_text())
    assert "modules" in bb_data
    assert "http_2xx" in bb_data["modules"]
    assert "tcp_connect" in bb_data["modules"]

    # Loki config
    loki_cfg = ROOT / "observability/loki.yml"
    assert loki_cfg.exists()
    loki_data = yaml.safe_load(loki_cfg.read_text())
    assert loki_data.get("auth_enabled") is False

    # Grafana datasources
    ds_cfg = ROOT / "observability/grafana/provisioning/datasources/datasources.yml"
    assert ds_cfg.exists()
    ds_data = yaml.safe_load(ds_cfg.read_text())
    ds_names = [d["name"] for d in ds_data.get("datasources", [])]
    assert "Prometheus" in ds_names
    assert "Loki" in ds_names
    assert "Jaeger" in ds_names

    # Grafana dashboard JSON
    dash_json = ROOT / "observability/grafana/dashboards/local-stack.json"
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

    # Verify dry-run outputs
    info_output = subprocess.check_output(
        ["make", "-n", "observability-dev-info"],
        cwd=ROOT,
        text=True,
    )
    assert "Grafana:    http://127.0.0.1:13001" in info_output
    assert "Prometheus: http://127.0.0.1:19090" in info_output


def test_docker_compose_config_validates_successfully() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.observability.yml",
            "config",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

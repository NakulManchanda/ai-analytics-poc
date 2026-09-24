# 0061 — Local Grafana observability stack for health, metrics, and logs

## Goal

Deliver issue #128 by extending the local observability Compose overlay
(`docker-compose.observability.yml`) so Grafana becomes the unified operational
dashboard for service health, metrics, and logs, while Jaeger remains the
dedicated distributed-trace explorer.

Also standardizes repository workflow guidance to store temporary AI scratch state
under `.vscode/myfiles/<issue>-<slug>/` and keep branch names tool-agnostic
(`feat/<issue>-<slug>`).

## Starting point

Prerequisite observability work was established in PR #117 (OTEL Collector + Jaeger
trace skeleton) and PR #119 (W3C trace propagation across App ⟷ MCP). The existing
observability overlay provided distributed trace visualization on port 16686, but
had no dashboarding for service availability, real-time metrics scraping, or
centralized container log searching.

## Decisions and changes

1. **Prometheus Blackbox Exporter (`observability/blackbox.yml`):**
   - Configured `http_2xx` and `tcp_connect` modules to perform active health probing
     against application services (`app:8000/health`, `mcp:8000/health`, `jaeger:16686/`)
     and TCP endpoints (`redis:6379`, `otel-collector:4318`).
2. **Prometheus Scrape Configuration (`observability/prometheus.yml`):**
   - Defined scrape targets for Blackbox HTTP and TCP probes with human-readable
     `service` labels (`app`, `mcp`, `jaeger`, `redis`, `otel-collector`).
   - Enables evaluation of `probe_success` and `probe_duration_seconds`.
3. **Loki Single-Binary Storage (`observability/loki.yml`):**
   - Configured local filesystem TSDB storage (schema v13) with no authentication
     for lightweight local operation.
4. **Grafana Alloy Container Log Shipping (`observability/alloy.alloy`):**
   - Utilizes Docker socket discovery to stream stdout/stderr logs from Compose
     containers directly to Loki with `service`, `container`, and `project` labels.
5. **Grafana Provisioning & Local Stack Dashboard:**
   - Pre-provisioned datasources (`observability/grafana/provisioning/datasources/datasources.yml`)
     for Prometheus, Loki, and Jaeger.
   - Pre-provisioned dashboard (`observability/grafana/dashboards/local-stack.json`)
     showing:
     - Real-time service up/down status stat panels (`probe_success`).
     - Service health check duration line graphs (`probe_duration_seconds`).
     - Live container logs panel querying `{service=~"app|mcp|otel-collector"}`.
6. **Compose Overlay Integration (`docker-compose.observability.yml`):**
   - Integrated `prometheus`, `blackbox-exporter`, `loki`, `alloy`, and `grafana`
     with configurable host port environment variables:
     - `GRAFANA_PORT` (default `13001`)
     - `PROMETHEUS_PORT` (default `19090`)
     - `LOKI_PORT` (default `13100`)
     - `BLACKBOX_PORT` (default `19115`)
   - Anonymous Admin authentication enabled for Grafana to allow instant local browsing.
7. **Makefile & CLI Experience:**
   - Updated `observability-dev-info` to print Grafana (`http://127.0.0.1:13001`) and
     Prometheus (`http://127.0.0.1:19090`) URLs alongside Web and Jaeger.
   - Updated `observability-dev-up` and `observability-dev-down` with port variables.
8. **Automated Verification:**
   - Added `tests/scripts/test_observability_compose.py` validating compose service
     declarations, configuration syntax, Makefile output, and compose config rendering.

## Verification

- `uv run --project services/app pytest tests/scripts/test_observability_compose.py`: 4 passed in 0.55s.
- `docker compose -f docker-compose.yml -f docker-compose.observability.yml config`: Validated cleanly.
- `make -n observability-dev-info`: Verified formatted URL output.
- Full service test suites (`mcp-test` 16 passed, `app` 144 passed).

## PR and merge state

- Worktree: `.worktrees/128-local-grafana`
- Branch: `feat/128-local-grafana`
- Pull Request: #129

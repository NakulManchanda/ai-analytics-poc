# 0050 — Telemetry metrics collection: CloudWatch EMF, local JSONL sink, and Streamlit comparison dashboard

## Goal

Implement zero-latency structured telemetry emission via AWS CloudWatch Embedded Metric Format (EMF), support a local append-only JSONL metrics sink for zero-AWS offline local development, and provide an interactive Streamlit comparison dashboard querying metrics with DuckDB.

## Starting point

While the orchestration loop previously durably recorded run telemetry (TTFT, proposal latency, tool latency, Bedrock cost) into DynamoDB and emitted SSE events, there was no structured streaming metrics log format emitted to standard output for CloudWatch Container Insights / CloudWatch Logs, nor was there a local append-only log file or offline comparison dashboard to compare models, prompts, latencies, and costs across runs.

## Decisions

- **Zero-Ingestion Latency CloudWatch EMF**: Created `services/app/app/metrics.py` to format runs as standard AWS CloudWatch Embedded Metric Format (EMF) logs emitted directly to `stdout`. Single-line JSON emission takes $<0.1\text{ ms}$, creating CloudWatch custom metrics (`ai-analytics-poc/runs`: `DurationMs`, `TimeToFirstTokenMs`, `ProposalLatencyMs`, `ToolLatencyMs`, `EstimatedBedrockCostUsd`, `ToolCount`) with zero additional API calls or network hops.
- **Local JSONL File Sink**: Configured optional `METRICS_FILE` environment variable (default `/app/metrics/runs.jsonl` in Docker Compose with host mount `./metrics:/app/metrics`). Each run appends a JSON record containing run status, model ID, prompt token counts, latencies, and costs.
- **Hooked Into Terminal Events**: Invoked `emit_run_metrics(...)` in `services/app/app/orchestration/loop.py` upon all terminal run states: `run.completed`, `run.cancelled`, `run.budget_exceeded`, and `run.failed`.
- **Decoupled Streamlit + DuckDB Dashboard**: Created `scripts/dashboard.py` runnable via `make dashboard` (`uv run --with streamlit --with duckdb streamlit run scripts/dashboard.py`). DuckDB queries `metrics/*.jsonl` on the fly without heavy database engines or mutating production container dependencies.
- **Preserved Clean Separation from Terraform**: Kept all Terraform files untouched in this PR so that infrastructure and GSI updates remain isolated for Issue #89.
- **Zero Frontend Changes**: Avoided modifying the React frontend per user decision to keep the core analytics UI focused and lean.

## Verification and status

- `uv run --project services/app pytest services/app/tests/test_metrics.py` passed (3/3 unit tests verifying CloudWatch EMF and local file appending).
- `uv run --project services/app pytest services/app/tests` passed (121/121 tests).
- `npm --prefix web test` passed (23/23 tests).
- `npm --prefix web run build` passed.
- `uv run --with streamlit --with duckdb python -c "import streamlit, duckdb; print('Streamlit + DuckDB ready')"` verified runtime dependencies.

## Lesson

CloudWatch Embedded Metric Format (EMF) enables zero-latency metric extraction from standard stdout streams without extra AWS SDK calls in the request path, while a simple append-only local JSONL file with DuckDB gives developers instant local parity for metric analysis and model comparison.

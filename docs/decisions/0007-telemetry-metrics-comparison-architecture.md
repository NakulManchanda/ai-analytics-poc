# ADR 0007 — Telemetry, CloudWatch Metrics, and Local Comparison Architecture

## Status

Accepted.

## Context

As the platform transitions from text-only analytics (v1–v3.1) into realtime voice input, speech synthesis, barge-in, and multimodal comparisons (v4–v8), evaluating and improving performance requires granular telemetry. 

Currently, runs persist point-in-time telemetry in DynamoDB under `RUN#<run_id>` / `METADATA`. While effective for single-run hydration in the React UI, comparing performance across runs, models, and milestones currently requires a full DynamoDB table scan. 

We need a unified, low-overhead telemetry architecture that:
1. Introduces practically zero latency to the live analytical execution loop.
2. Supports asynchronous CloudWatch Metrics extraction for AWS production deployments.
3. Enables offline, local developer benchmarking and visual comparison without frontend UI bloat.
4. Provides standard, downloadable 12-factor log exports from deployed AWS environments.
5. Establishes a rigorous question-driven metrics framework for upcoming voice and model comparisons.

---

## Decisions

### 1. Dual Ingestion: 12-Factor `stdout` (EMF) and Optional Local Append
- **Production Path**: The application writes a single structured JSON line to `stdout` containing the AWS Embedded Metric Format (`_aws.CloudWatchMetrics`) specification when each run completes or cancels. This introduces $< 0.1\text{ ms}$ overhead in Python and zero blocking network calls. AWS ECS/CloudWatch Logs ingests and parses these logs asynchronously into CloudWatch Metrics.
- **Local Developer Path**: When running locally via Docker Compose, a mounted directory (`./metrics:/app/metrics`) allows the application to append run summaries to `runs.jsonl`. Appending one string to an open file buffer takes $\approx 0.03\text{ ms}$, adding negligible overhead.

### 2. DynamoDB GSI via Terraform
- Update `infra/terraform/dynamodb.tf` to introduce a Global Secondary Index (`GSI_EntityTypeDate`) on the existing `application-state` table:
  - Partition Key (`gsi1pk`): `entity_type` (e.g., `"run"`, `"run#v3.1"`, `"run#v4"`)
  - Sort Key (`gsi1sk`): `started_at` (ISO timestamp)
  - Projection: Selected telemetry metrics (`ttft_ms`, `end_to_end_latency_ms`, `estimated_cost_usd`, `model`, `status`).
- This eliminates table scans for cross-run queries while preserving on-demand zero-base-cost pricing and table integrity.

### 3. Local Comparison Dashboard via Streamlit (`make dashboard`)
- Rather than bloating the production React UI with charting libraries and comparative state, developer comparison tooling is isolated to a standalone **Streamlit** script (`scripts/dashboard.py`).
- Run via an isolated Make target using ephemeral dependencies:
  ```makefile
  dashboard: ## Run local Streamlit metrics & model comparison dashboard
  	uv run --with streamlit --with duckdb streamlit run scripts/dashboard.py
  ```
- Uses embedded **DuckDB** to execute instant SQL aggregations (`AVG`, `QUANTILE_CONT` p50/p90/p95, group-bys) directly over `metrics/runs.jsonl`.

### 4. Portable Log Extraction from Public Deployments
- All deployed telemetry is 12-factor standard output. Operators can download deployment metrics and run traces from CloudWatch Logs at any time into a local JSONL file:
  ```bash
  aws logs tail /aws/ecs/ai-app \
    --filter-pattern '{ $.entity_type = "run" }' \
    --format json > local_downloaded_runs.jsonl
  ```
- Downloaded logs can be analyzed immediately in the local Streamlit dashboard or DuckDB terminal with zero transformation.

---

## Sample Log & Event Shapes

### CloudWatch Embedded Metric Format (EMF) Payload (Emitted to `stdout`)
```json
{
  "_aws": {
    "Timestamp": 1757301000000,
    "CloudWatchMetrics": [
      {
        "Namespace": "AIAnalyticsPOC",
        "Dimensions": [["Milestone"], ["Model"], ["TurnType"]],
        "Metrics": [
          { "Name": "EndToEndLatency", "Unit": "Milliseconds" },
          { "Name": "ProposalLLMLatency", "Unit": "Milliseconds" },
          { "Name": "ToolExecutionLatency", "Unit": "Milliseconds" },
          { "Name": "FinalAnswerLLMLatency", "Unit": "Milliseconds" },
          { "Name": "TimeToOneFirstToken", "Unit": "Milliseconds" },
          { "Name": "InputTokens", "Unit": "Count" },
          { "Name": "OutputTokens", "Unit": "Count" },
          { "Name": "TotalCostUSD", "Unit": "None" }
        ]
      }
    ]
  },
  "entity_type": "run",
  "run_id": "run_4393fe053afe4eb2",
  "conversation_id": "conv_acbf254280ac4d9e",
  "milestone": "v3.1-streaming",
  "model": "amazon.nova-micro-v1:0",
  "turn_type": "text",
  "status": "completed",
  "EndToEndLatency": 6138,
  "ProposalLLMLatency": 951,
  "ToolExecutionLatency": 2144,
  "FinalAnswerLLMLatency": 2002,
  "TimeToOneFirstToken": 397,
  "InputTokens": 987,
  "OutputTokens": 200,
  "TotalCostUSD": 0.005961,
  "started_at": "2026-09-08T02:55:49.511475+00:00",
  "completed_at": "2026-09-08T02:55:55.794945+00:00"
}
```

### Local JSONL Metric Line (`metrics/runs.jsonl`)
```json
{"run_id": "run_4393fe053afe4eb2", "milestone": "v3.1", "model": "amazon.nova-micro-v1:0", "turn_type": "text", "status": "completed", "end_to_end_latency_ms": 6138, "proposal_llm_latency_ms": 951, "tool_latency_ms": 2144, "final_answer_llm_latency_ms": 2002, "ttft_latency_ms": 397, "input_tokens": 987, "output_tokens": 200, "estimated_cost_usd": 0.005961, "started_at": "2026-09-08T02:55:49.511475+00:00"}
```

---

## Metrics Question Guide

The metrics collection framework is designed to provide definitive, data-backed answers to the following engineering and architecture questions:

### 1. Voice Latency Waterfall (Milestones v4 & v5)
- *What is the total conversational latency from end-of-speech to start-of-audio?*
  $$\text{Total Turn Latency} = \text{STT Finalize} + \text{Tool Execution} + \text{LLM TTFT} + \text{TTS TTFA}$$
- *Benchmark Targets*:
  - STT Finalize Latency: $< 200\text{ ms}$
  - LLM TTFT (Nova Micro): $< 400\text{ ms}$
  - TTS Time to First Audio (TTFA): $< 200\text{ ms}$
  - Total Target Turn Latency: $< 800\text{ ms}$

### 2. Barge-In & Interruption Efficiency (Milestone v6)
- *How fast does audio halt when user speech is detected?* Target: $< 150\text{ ms}$.
- *What is the LLM cancellation abort latency?* Does Bedrock stop streaming within 100 ms of cancellation?
- *How many output tokens and audio frames were avoided by interrupting early?*

### 3. Model vs. Model Benchmarking
- *Which foundation model offers the best balance of TTFT, reasoning accuracy, and cost for governed analytics?*
- *What is the p50, p90, and p95 latency for each candidate model across 100 standard NYC taxi prompts?*
- *Does model precision degrade as the multi-turn conversation context window expands?*

### 4. Cascaded Voice vs. Native Speech-to-Speech (Milestone v8)
- *Does native speech-to-speech reduce conversational turn latency compared to the specialist cascade ($\text{STT} \to \text{LLM} \to \text{TTS}$)?*
- *Can native speech-to-speech models output structured MCP tool arguments as accurately as specialized text LLMs?*
- *What is the cost comparison per conversational minute between cascaded components vs. native speech APIs?*

---

## Consequences

- **Preserves POC Boundaries**: Avoids introducing Kafka, Prometheus, Grafana, OpenSearch, or a separate timeseries database.
- **Frontend Decoupling**: The React application remains focused exclusively on its role as the analytical control room; developer comparison dashboards live in a lightweight Python script.
- **Operator Authority**: Adding the DynamoDB GSI requires a planned Terraform apply, which will be bundled with the remote state milestone (Issue #89).

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

CLOUDWATCH_NAMESPACE = "AIAnalyticsPOC"


def format_cloudwatch_emf(
    *,
    run_id: str,
    conversation_id: str,
    milestone: str,
    model: str,
    turn_type: str,
    status: str,
    end_to_end_latency_ms: int | None = None,
    proposal_llm_latency_ms: int | None = None,
    tool_latency_ms: int | None = None,
    final_answer_llm_latency_ms: int | None = None,
    ttft_latency_ms: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Format run execution metrics into AWS CloudWatch Embedded Metric Format (EMF)."""
    now = timestamp or datetime.now(UTC)
    ts_ms = int(now.timestamp() * 1000)

    metric_definitions: list[dict[str, str]] = [
        {"Name": "InputTokens", "Unit": "Count"},
        {"Name": "OutputTokens", "Unit": "Count"},
        {"Name": "TotalCostUSD", "Unit": "None"},
    ]
    if end_to_end_latency_ms is not None:
        metric_definitions.append({"Name": "EndToEndLatency", "Unit": "Milliseconds"})
    if proposal_llm_latency_ms is not None:
        metric_definitions.append({"Name": "ProposalLLMLatency", "Unit": "Milliseconds"})
    if tool_latency_ms is not None:
        metric_definitions.append({"Name": "ToolExecutionLatency", "Unit": "Milliseconds"})
    if final_answer_llm_latency_ms is not None:
        metric_definitions.append({"Name": "FinalAnswerLLMLatency", "Unit": "Milliseconds"})
    if ttft_latency_ms is not None:
        metric_definitions.append({"Name": "TimeToOneFirstToken", "Unit": "Milliseconds"})

    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": ts_ms,
            "CloudWatchMetrics": [
                {
                    "Namespace": CLOUDWATCH_NAMESPACE,
                    "Dimensions": [["Milestone"], ["Model"], ["TurnType"]],
                    "Metrics": metric_definitions,
                }
            ],
        },
        "entity_type": "run",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "Milestone": milestone,
        "Model": model,
        "TurnType": turn_type,
        "status": status,
        "InputTokens": input_tokens,
        "OutputTokens": output_tokens,
        "TotalCostUSD": estimated_cost_usd,
        # Flat fields for local querying / DuckDB SQL ease
        "end_to_end_latency_ms": end_to_end_latency_ms,
        "proposal_llm_latency_ms": proposal_llm_latency_ms,
        "tool_latency_ms": tool_latency_ms,
        "final_answer_llm_latency_ms": final_answer_llm_latency_ms,
        "ttft_latency_ms": ttft_latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "timestamp": now.isoformat(),
    }
    if end_to_end_latency_ms is not None:
        payload["EndToEndLatency"] = end_to_end_latency_ms
    if proposal_llm_latency_ms is not None:
        payload["ProposalLLMLatency"] = proposal_llm_latency_ms
    if tool_latency_ms is not None:
        payload["ToolExecutionLatency"] = tool_latency_ms
    if final_answer_llm_latency_ms is not None:
        payload["FinalAnswerLLMLatency"] = final_answer_llm_latency_ms
    if ttft_latency_ms is not None:
        payload["TimeToOneFirstToken"] = ttft_latency_ms

    return payload


def emit_run_metrics(
    *,
    run_id: str,
    conversation_id: str,
    milestone: str = "v3.1",
    model: str = "amazon.nova-micro-v1:0",
    turn_type: str = "text",
    status: str,
    telemetry: dict[str, Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    metrics_file: str | None = None,
) -> dict[str, Any]:
    """Emit EMF to stdout and optionally append to a local JSONL sink."""
    t = telemetry or {}
    ttft_raw = t.get("ttft")
    ttft_ms = None
    if isinstance(ttft_raw, dict) and ttft_raw.get("available") and isinstance(ttft_raw.get("latency_ms"), (int, float)):
        ttft_ms = int(ttft_raw["latency_ms"])

    record = format_cloudwatch_emf(
        run_id=run_id,
        conversation_id=conversation_id,
        milestone=milestone,
        model=model,
        turn_type=turn_type,
        status=status,
        end_to_end_latency_ms=t.get("end_to_end_latency_ms"),
        proposal_llm_latency_ms=t.get("proposal_llm_latency_ms"),
        tool_latency_ms=t.get("tool_latency_ms"),
        final_answer_llm_latency_ms=t.get("final_answer_llm_latency_ms"),
        ttft_latency_ms=ttft_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )

    # 1. 12-factor stdout log for CloudWatch / container runtime
    line = json.dumps(record)
    logger.info("%s", line)

    # 2. Local append-only JSONL sink if configured
    target_file = metrics_file or os.getenv("METRICS_FILE")
    if target_file:
        try:
            target_dir = os.path.dirname(target_file)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as err:
            logger.warning("Failed to append run metric to %s: %s", target_file, err)

    return record

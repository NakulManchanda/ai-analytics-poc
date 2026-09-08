import json
import logging
from pathlib import Path

from app.metrics import emit_run_metrics, format_cloudwatch_emf


def test_format_cloudwatch_emf_structure() -> None:
    emf = format_cloudwatch_emf(
        run_id="run_123",
        conversation_id="conv_abc",
        milestone="v3.1",
        model="amazon.nova-micro-v1:0",
        turn_type="text",
        status="completed",
        end_to_end_latency_ms=500,
        proposal_llm_latency_ms=100,
        tool_latency_ms=150,
        final_answer_llm_latency_ms=250,
        ttft_latency_ms=80,
        input_tokens=1000,
        output_tokens=150,
        estimated_cost_usd=0.005,
    )

    assert "_aws" in emf
    cloudwatch_config = emf["_aws"]["CloudWatchMetrics"][0]
    assert cloudwatch_config["Namespace"] == "AIAnalyticsPOC"
    assert cloudwatch_config["Dimensions"] == [["Milestone"], ["Model"], ["TurnType"]]

    metric_names = [m["Name"] for m in cloudwatch_config["Metrics"]]
    assert "EndToEndLatency" in metric_names
    assert "ProposalLLMLatency" in metric_names
    assert "ToolExecutionLatency" in metric_names
    assert "FinalAnswerLLMLatency" in metric_names
    assert "TimeToOneFirstToken" in metric_names
    assert "InputTokens" in metric_names
    assert "OutputTokens" in metric_names
    assert "TotalCostUSD" in metric_names

    # Verify flat attributes for DuckDB querying
    assert emf["run_id"] == "run_123"
    assert emf["conversation_id"] == "conv_abc"
    assert emf["Milestone"] == "v3.1"
    assert emf["Model"] == "amazon.nova-micro-v1:0"
    assert emf["status"] == "completed"
    assert emf["end_to_end_latency_ms"] == 500
    assert emf["ttft_latency_ms"] == 80
    assert emf["total_tokens"] == 1150
    assert emf["estimated_cost_usd"] == 0.005


def test_emit_run_metrics_appends_to_local_file(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    metrics_file = tmp_path / "subdir" / "runs.jsonl"

    record = emit_run_metrics(
        run_id="run_456",
        conversation_id="conv_def",
        milestone="v3.1",
        model="amazon.nova-micro-v1:0",
        turn_type="text",
        status="completed",
        telemetry={
            "end_to_end_latency_ms": 350,
            "ttft": {"available": True, "latency_ms": 45},
        },
        input_tokens=500,
        output_tokens=80,
        estimated_cost_usd=0.002,
        metrics_file=str(metrics_file),
    )

    assert record["run_id"] == "run_456"
    assert record["ttft_latency_ms"] == 45

    # Verify stdout / logger contains serialized EMF JSON
    found_log = False
    for message in caplog.messages:
        try:
            parsed = json.loads(message)
            if parsed.get("run_id") == "run_456" and "_aws" in parsed:
                found_log = True
                break
        except Exception:
            pass
    assert found_log, "EMF payload was not logged to stdout"

    # Verify file was created and contains the JSON line
    assert metrics_file.exists()
    lines = metrics_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    file_record = json.loads(lines[0])
    assert file_record["run_id"] == "run_456"
    assert file_record["end_to_end_latency_ms"] == 350
    assert file_record["ttft_latency_ms"] == 45


def test_emit_run_metrics_handles_unwritable_file_gracefully(caplog) -> None:
    caplog.set_level(logging.WARNING)

    # Passing an impossible file path should log a warning, not raise an unhandled exception
    record = emit_run_metrics(
        run_id="run_789",
        conversation_id="conv_ghi",
        status="cancelled",
        metrics_file="/proc/forbidden/invalid/path/runs.jsonl",
    )

    assert record["run_id"] == "run_789"
    assert record["status"] == "cancelled"

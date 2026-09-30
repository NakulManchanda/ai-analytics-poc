from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.benchmarks.metrics_scraper import (
    MetricsDelta,
)
from app.benchmarks.replayer import ReplaySummary, TurnResult

from services.app.scripts.run_scenario import (
    async_main,
    generate_markdown_report,
)


def test_generate_markdown_report():
    summary = ReplaySummary(
        scenario_name="test_scenario",
        total_conversations=1,
        total_turns=2,
        successful_turns=2,
        failed_turns=0,
        duration_seconds=1.5,
        requests_per_second=1.33,
        latency_ms={"p50": 100.0, "p90": 120.0, "p95": 125.0, "p99": 130.0},
        ttft_ms={"p50": 20.0, "p90": 25.0, "p95": 27.0, "p99": 30.0},
        total_prompt_tokens=500,
        total_completion_tokens=100,
        turn_results=[
            TurnResult(
                conversation_id="conv_1",
                turn_index=0,
                prompt="Which pickup zones have the most trips?",
                status="completed",
                client_duration_ms=100.0,
                tokens_in=250,
                tokens_out=50,
            ),
            TurnResult(
                conversation_id="conv_1",
                turn_index=1,
                prompt="What are the peak hours and busiest times for taxi rides in NYC?",
                status="completed",
                client_duration_ms=120.0,
                tokens_in=250,
                tokens_out=50,
            ),
        ],
    )
    delta = MetricsDelta(
        duration_seconds=1.5,
        prefix_cache_hits=1.0,
        prefix_cache_queries=2.0,
        prefix_cache_hit_rate_pct=50.0,
        prompt_tokens=500.0,
        generation_tokens=100.0,
        avg_ttft_seconds=0.02,
        avg_queue_time_seconds=0.005,
        gpu_cache_usage_post=0.35,
        avg_prompt_throughput=1200.0,
        avg_generation_throughput=200.0,
    )

    md = generate_markdown_report(
        summary=summary,
        metrics_delta=delta,
        target_url="http://127.0.0.1:8080",
        metrics_url="http://127.0.0.1:18001/metrics",
        strategy="manual",
        timestamp_str="2026-09-30T00:00:00Z",
    )
    assert "# Benchmark Evidence: `test_scenario`" in md
    assert "50.0%" in md
    assert "100.0" in md
    assert "Which pickup zones" in md


@pytest.mark.anyio
async def test_async_main_e2e(tmp_path: Path, monkeypatch):
    import httpx

    # Create dummy scenario in tmp_path
    scen_file = tmp_path / "dummy.json"
    scen_file.write_text(
        json.dumps(
            {
                "name": "dummy",
                "description": "Dummy scenario",
                "concurrency": 1,
                "strategy": "manual",
                "target_endpoint_type": "app_runs",
                "conversations": [
                    {
                        "conversation_id_prefix": "c1",
                        "turns": [
                            {"question": "Which pickup zones have the most trips?"}
                        ],
                    }
                ],
            }
        )
    )

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            return httpx.Response(
                202,
                json={
                    "conversation_id": "c1",
                    "message_id": "m1",
                    "run_id": "r1",
                    "events_url": "/api/runs/r1/events",
                },
            )
        elif "/events" in url_str:
            completed_str = json.dumps(
                {
                    "status": "completed",
                    "input_tokens": 100,
                    "output_tokens": 20,
                }
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"event: run.completed\ndata: {completed_str}\n\n",
            )
        elif "/metrics" in url_str:
            return httpx.Response(
                200,
                text="vllm:prefix_cache_hits_total 10.0\nvllm:prefix_cache_queries_total 20.0\n",
            )
        return httpx.Response(404)

    # Monkeypatch httpx.AsyncClient to use mock transport
    original_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(mock_handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_factory)

    class MockArgs:
        scenario = str(scen_file)
        target_url = "http://test:8080"
        metrics_url = "http://test:18001/metrics"
        concurrency = None
        strategy = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")

    res = await async_main(MockArgs())
    assert res == 0

    # Verify generated evidence
    ev_files = list((tmp_path / "evidence").glob("dummy_manual_*.json"))
    assert len(ev_files) == 1
    with open(ev_files[0]) as f:
        data = json.load(f)
        assert data["summary"]["successful_turns"] == 1
        assert data["metrics_delta"] is not None
        # pre and post had same values -> delta=0
        assert data["metrics_delta"]["prefix_cache_hit_rate_pct"] == 0.0

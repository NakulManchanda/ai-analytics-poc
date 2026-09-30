from __future__ import annotations

import httpx
import pytest
from app.benchmarks.metrics_scraper import (
    PrometheusMetricSnapshot,
    compute_metrics_delta,
    parse_labels,
)

SAMPLE_METRICS_V1 = """
# HELP vllm:prefix_cache_hits_total Number of prefix cache hits.
# TYPE vllm:prefix_cache_hits_total counter
vllm:prefix_cache_hits_total{model_name="Qwen/Qwen3-0.6B"} 100.0 1620000000
# HELP vllm:prefix_cache_queries_total Number of prefix cache queries.
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{model_name="Qwen/Qwen3-0.6B"} 200.0
# HELP vllm:prompt_tokens_total Number of prompt tokens.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total 5000.0
# HELP vllm:generation_tokens_total Number of generation tokens.
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total 1000.0
# HELP vllm:time_to_first_token_seconds Histogram of TTFT.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_count 10.0
vllm:time_to_first_token_seconds_sum 2.5
# HELP vllm:request_queue_time_seconds Histogram of queue time.
vllm:request_queue_time_seconds_count 10.0
vllm:request_queue_time_seconds_sum 0.5
# HELP vllm:gpu_cache_usage_factor GPU KV-cache usage.
# TYPE vllm:gpu_cache_usage_factor gauge
vllm:gpu_cache_usage_factor 0.25
vllm:avg_prompt_throughput_tok_per_s 1500.0
vllm:avg_generation_throughput_tok_per_s 250.0
"""

SAMPLE_METRICS_V2 = """
vllm:prefix_cache_hits_total{model_name="Qwen/Qwen3-0.6B"} 180.0
vllm:prefix_cache_queries_total{model_name="Qwen/Qwen3-0.6B"} 300.0
vllm:prompt_tokens_total 7500.0
vllm:generation_tokens_total 1500.0
vllm:time_to_first_token_seconds_count 15.0
vllm:time_to_first_token_seconds_sum 3.5
vllm:request_queue_time_seconds_count 15.0
vllm:request_queue_time_seconds_sum 0.75
vllm:gpu_cache_usage_factor 0.45
vllm:avg_prompt_throughput_tok_per_s 1800.0
vllm:avg_generation_throughput_tok_per_s 300.0
"""

SAMPLE_METRICS_RESET = """
vllm:prefix_cache_hits_total{model_name="Qwen/Qwen3-0.6B"} 10.0
vllm:prefix_cache_queries_total{model_name="Qwen/Qwen3-0.6B"} 20.0
vllm:prompt_tokens_total 500.0
vllm:generation_tokens_total 100.0
"""


def test_parse_labels():
    labels = parse_labels(
        'model_name="Qwen/Qwen3-0.6B",worker_id="0",quote="hello \\"world\\""'
    )
    assert labels["model_name"] == "Qwen/Qwen3-0.6B"
    assert labels["worker_id"] == "0"
    assert labels["quote"] == 'hello "world"'


def test_snapshot_from_text():
    snapshot = PrometheusMetricSnapshot.from_text(SAMPLE_METRICS_V1, timestamp=1000.0)
    assert snapshot.timestamp == 1000.0
    assert snapshot.get_sum("vllm:prefix_cache_hits_total") == 100.0
    assert snapshot.get_sum("vllm:prefix_cache_queries_total") == 200.0
    assert snapshot.get_first_value("vllm:gpu_cache_usage_factor") == 0.25
    assert snapshot.has_metric("vllm:prompt_tokens_total")
    # Alternate naming
    assert snapshot.get_sum("vllm_prefix_cache_hits_total") == 100.0


def test_compute_metrics_delta_normal():
    snap_before = PrometheusMetricSnapshot.from_text(
        SAMPLE_METRICS_V1, timestamp=1000.0
    )
    snap_after = PrometheusMetricSnapshot.from_text(SAMPLE_METRICS_V2, timestamp=1010.0)

    delta = compute_metrics_delta(snap_before, snap_after)
    assert delta.duration_seconds == 10.0
    assert not delta.counter_reset_detected
    assert delta.prefix_cache_hits == 80.0
    assert delta.prefix_cache_queries == 100.0
    assert delta.prefix_cache_hit_rate_pct == 80.0  # 80 / 100 = 80%
    assert delta.prompt_tokens == 2500.0
    assert delta.generation_tokens == 500.0

    # TTFT: 1.0s sum / 5 requests = 0.20s
    assert delta.avg_ttft_seconds == pytest.approx(0.20)
    # Queue: 0.25s sum / 5 requests = 0.05s
    assert delta.avg_queue_time_seconds == pytest.approx(0.05)

    assert delta.gpu_cache_usage_post == 0.45
    assert delta.avg_prompt_throughput == 1800.0
    assert delta.avg_generation_throughput == 300.0


def test_compute_metrics_delta_reset():
    snap_before = PrometheusMetricSnapshot.from_text(
        SAMPLE_METRICS_V1, timestamp=1000.0
    )
    snap_after = PrometheusMetricSnapshot.from_text(
        SAMPLE_METRICS_RESET, timestamp=1010.0
    )

    delta = compute_metrics_delta(snap_before, snap_after)
    assert delta.counter_reset_detected
    assert delta.prefix_cache_hits == 10.0
    assert delta.prefix_cache_queries == 20.0
    assert delta.prefix_cache_hit_rate_pct == 50.0


@pytest.mark.anyio
async def test_scrape_with_mock_client():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_METRICS_V1)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        snapshot = await PrometheusMetricSnapshot.scrape(
            "http://mock-worker:8000/metrics", client=client
        )
        assert snapshot.get_sum("vllm:prefix_cache_hits_total") == 100.0

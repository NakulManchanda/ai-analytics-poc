#!/usr/bin/env python3
"""Execute a benchmark scenario, optionally scrape Prometheus metrics, and generate evidence."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.benchmarks.metrics_scraper import (
    MetricsDelta,
    PrometheusMetricSnapshot,
    compute_metrics_delta,
)
from app.benchmarks.replayer import ReplaySummary, ScenarioReplayer
from app.scenarios.loader import load_scenario
from app.scenarios.models import ScenarioConfig

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def generate_markdown_report(
    summary: ReplaySummary,
    metrics_delta: MetricsDelta | None,
    target_url: str,
    metrics_url: str | None,
    strategy: str,
    timestamp_str: str,
) -> str:
    lines: list[str] = [
        f"# Benchmark Evidence: `{summary.scenario_name}`",
        "",
        f"**Date:** {timestamp_str}  ",
        f"**Target URL:** `{target_url}`  ",
        f"**Strategy:** `{strategy}`  ",
    ]
    if metrics_url:
        lines.append(f"**Metrics URL:** `{metrics_url}`  ")
    lines.extend(
        [
            "",
            "## 1. Scenario Execution Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| **Scenario Name** | `{summary.scenario_name}` |",
            f"| **Total Conversations** | {summary.total_conversations} |",
            f"| **Total Turns** | {summary.total_turns} |",
            f"| **Successful Turns** | {summary.successful_turns} |",
            f"| **Failed Turns** | {summary.failed_turns} |",
            f"| **Total Duration** | {summary.duration_seconds:.2f} s |",
            f"| **Client Throughput** | {summary.requests_per_second:.2f} req/s |",
            f"| **Prompt Tokens** | {summary.total_prompt_tokens} |",
            f"| **Completion Tokens** | {summary.total_completion_tokens} |",
            "",
            "## 2. Client Latency & TTFT Percentiles",
            "",
            "| Percentile | Latency (ms) | TTFT (ms) |",
            "|---|---|---|",
            f"| **p50** | {summary.latency_ms.get('p50', 0.0):.1f} | "
            f"{summary.ttft_ms.get('p50', 0.0):.1f} |",
            f"| **p90** | {summary.latency_ms.get('p90', 0.0):.1f} | "
            f"{summary.ttft_ms.get('p90', 0.0):.1f} |",
            f"| **p95** | {summary.latency_ms.get('p95', 0.0):.1f} | "
            f"{summary.ttft_ms.get('p95', 0.0):.1f} |",
            f"| **p99** | {summary.latency_ms.get('p99', 0.0):.1f} | "
            f"{summary.ttft_ms.get('p99', 0.0):.1f} |",
            "",
        ]
    )

    if metrics_delta:
        lines.extend(
            [
                "## 3. Worker Prometheus Metrics Delta",
                "",
                "| Metric | Delta / Snapshot Value |",
                "|---|---|",
                f"| **Prefix Cache Hits (Δ)** | {metrics_delta.prefix_cache_hits:.0f} |",
                f"| **Prefix Cache Queries (Δ)** | {metrics_delta.prefix_cache_queries:.0f} |",
                f"| **Prefix Cache Hit Rate** | {metrics_delta.prefix_cache_hit_rate_pct:.1f}% |",
                f"| **Worker Prompt Tokens (Δ)** | {metrics_delta.prompt_tokens:.0f} |",
                f"| **Worker Generation Tokens (Δ)** | {metrics_delta.generation_tokens:.0f} |",
            ]
        )
        if metrics_delta.avg_ttft_seconds is not None:
            ttft_val = metrics_delta.avg_ttft_seconds * 1000.0
            lines.append(f"| **Avg Worker TTFT** | {ttft_val:.1f} ms |")
        if metrics_delta.avg_queue_time_seconds is not None:
            queue_val = metrics_delta.avg_queue_time_seconds * 1000.0
            lines.append(f"| **Avg Request Queue Time** | {queue_val:.1f} ms |")
        if metrics_delta.gpu_cache_usage_post is not None:
            gpu_val = metrics_delta.gpu_cache_usage_post * 100.0
            lines.append(f"| **Post-burst GPU Cache Usage** | {gpu_val:.1f}% |")
        if metrics_delta.avg_prompt_throughput is not None:
            lines.append(
                f"| **Prompt Throughput** | {metrics_delta.avg_prompt_throughput:.1f} tok/s |"
            )
        if metrics_delta.avg_generation_throughput is not None:
            lines.append(
                f"| **Gen Throughput** | {metrics_delta.avg_generation_throughput:.1f} tok/s |"
            )
        if metrics_delta.counter_reset_detected:
            lines.append("| **Warning** | Counter reset detected during burst! |")
        lines.append("")

    lines.extend(
        [
            "## 4. Turn Details",
            "",
            "| Conv | Turn | Prompt | Status | Latency (ms) | In Tok | Out Tok |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for tr in summary.turn_results:
        short_prompt = tr.prompt.replace("|", "\\|")
        if len(short_prompt) > 40:
            short_prompt = short_prompt[:37] + "..."
        lines.append(
            f"| `{tr.conversation_id}` | {tr.turn_index + 1} | {short_prompt} | "
            f"`{tr.status}` | {tr.client_duration_ms:.1f} | {tr.tokens_in} | {tr.tokens_out} |"
        )
    lines.append("")
    return "\n".join(lines)


async def async_main(args: argparse.Namespace) -> int:
    config = load_scenario(args.scenario)

    if args.concurrency is not None:
        config.concurrency = args.concurrency
    if args.strategy is not None:
        config.strategy = args.strategy
        logger.info(
            "Scenario reporting strategy set to '%s'. (Note: Target application executes "
            "the strategy configured by its AGENT_STRATEGY env var at boot).",
            config.strategy,
        )
    endpoint_type = getattr(args, "endpoint_type", None)
    if endpoint_type is not None:
        config.target_endpoint_type = endpoint_type
    elif ":18080" in args.target_url or ":18001" in args.target_url or ":18002" in args.target_url:
        config.target_endpoint_type = "gateway_chat"

    # Re-validate scenario configuration after applying CLI overrides
    config = ScenarioConfig.model_validate(config.model_dump())

    logger.info(
        "Loaded scenario '%s' (%d convs, %d turns, concurrency=%d, strategy=%s)",
        config.name,
        config.total_conversations,
        config.total_turns,
        config.concurrency,
        config.strategy,
    )

    # 1. Pre-burst metrics scrape
    snap_before: PrometheusMetricSnapshot | None = None
    if args.metrics_url:
        try:
            logger.info("Scraping pre-burst metrics from %s...", args.metrics_url)
            snap_before = await PrometheusMetricSnapshot.scrape(args.metrics_url)
        except Exception as e:
            logger.warning("Failed to scrape pre-burst metrics: %s", e)

    # 2. Replay scenario
    logger.info("Starting scenario replay against %s...", args.target_url)
    replayer = ScenarioReplayer(
        config=config,
        target_base_url=args.target_url,
        timeout=args.timeout,
        use_sse=not args.no_sse,
    )
    summary = await replayer.run()
    logger.info(
        "Replay finished: %d/%d turns succeeded in %.2fs (%.2f req/s)",
        summary.successful_turns,
        summary.total_turns,
        summary.duration_seconds,
        summary.requests_per_second,
    )

    # 3. Post-burst metrics scrape
    snap_after: PrometheusMetricSnapshot | None = None
    metrics_delta: MetricsDelta | None = None
    if args.metrics_url and snap_before:
        try:
            logger.info("Scraping post-burst metrics from %s...", args.metrics_url)
            snap_after = await PrometheusMetricSnapshot.scrape(args.metrics_url)
            metrics_delta = compute_metrics_delta(snap_before, snap_after)
            logger.info(
                "Metrics delta: hits=%.0f, queries=%.0f, hit_rate=%.1f%%",
                metrics_delta.prefix_cache_hits,
                metrics_delta.prefix_cache_queries,
                metrics_delta.prefix_cache_hit_rate_pct,
            )
        except Exception as e:
            logger.warning("Failed to scrape post-burst metrics: %s", e)

    # 4. Save evidence artifacts
    now = datetime.datetime.now(datetime.UTC)
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{config.name}_{config.strategy}_{ts_str}"
    json_path = out_dir / f"{base_name}.json"
    md_path = out_dir / f"{base_name}.md"

    artifact_data: dict[str, Any] = {
        "timestamp": now.isoformat(),
        "scenario": config.model_dump(),
        "summary": summary.model_dump(),
        "metrics_delta": metrics_delta.model_dump() if metrics_delta else None,
        "target_url": args.target_url,
        "metrics_url": args.metrics_url,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(artifact_data, f, indent=2)
    logger.info("Wrote JSON evidence to %s", json_path)

    md_report = generate_markdown_report(
        summary=summary,
        metrics_delta=metrics_delta,
        target_url=args.target_url,
        metrics_url=args.metrics_url,
        strategy=config.strategy,
        timestamp_str=now.isoformat(),
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    logger.info("Wrote Markdown report to %s", md_path)

    return 0 if summary.failed_turns == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run benchmark scenario and scrape metrics deltas."
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Scenario name or file path (e.g. shared_prefix_fanout)",
    )
    parser.add_argument(
        "--target-url",
        default="http://127.0.0.1:8080",
        help="Base URL of application service (default: http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--metrics-url",
        default=None,
        help="URL of Prometheus /metrics endpoint (e.g. http://127.0.0.1:18001/metrics)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Override scenario concurrency",
    )
    parser.add_argument(
        "--strategy",
        choices=["manual", "crewai"],
        default=None,
        help="Override scenario strategy",
    )
    parser.add_argument(
        "--endpoint-type",
        choices=["app_runs", "gateway_chat"],
        default=None,
        help="Override scenario target endpoint type (default: auto/scenario setting)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Request/stream timeout in seconds (default: 120.0)",
    )
    parser.add_argument(
        "--no-sse",
        action="store_true",
        help="Disable SSE and poll /api/conversations instead",
    )
    parser.add_argument(
        "--output-dir",
        default="metrics/evidence",
        help="Directory to save evidence artifacts (default: metrics/evidence)",
    )

    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())

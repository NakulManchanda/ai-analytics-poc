"""Cold/warm time-to-first-token aggregation for controlled experiments."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class WarmupTTFTSummary:
    """The initial cold measurement and nearest-rank warm percentiles."""

    cold_ttft_ms: float
    warm_sample_count: int
    warm_ttft_p50_ms: float
    warm_ttft_p95_ms: float


def _nearest_rank(values: list[float], percentile: float) -> float:
    rank = math.ceil(percentile * len(values))
    return values[rank - 1]


def aggregate_warmup_ttft(samples: Sequence[float]) -> WarmupTTFTSummary:
    """Treat the first TTFT as cold and aggregate every later sample as warm."""
    if len(samples) < 2:
        raise ValueError("at least two samples are required: one cold and one warm")

    normalized: list[float] = []
    for sample in samples:
        if isinstance(sample, bool) or not isinstance(sample, (int, float)):
            raise ValueError("TTFT samples must be finite non-negative numbers")
        milliseconds = float(sample)
        if not math.isfinite(milliseconds) or milliseconds < 0:
            raise ValueError("TTFT samples must be finite non-negative numbers")
        normalized.append(milliseconds)

    warm_samples = sorted(normalized[1:])
    return WarmupTTFTSummary(
        cold_ttft_ms=normalized[0],
        warm_sample_count=len(warm_samples),
        warm_ttft_p50_ms=_nearest_rank(warm_samples, 0.50),
        warm_ttft_p95_ms=_nearest_rank(warm_samples, 0.95),
    )


def _measure_request_ttft(
    endpoint: str, model: str, prompt: str
) -> tuple[float, float]:
    """Measure true streaming TTFT (time to first SSE chunk) and total duration."""
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "max_tokens": 10,
            "stream": True,
        }
    ).encode("utf-8")
    req = Request(
        f"{endpoint.rstrip('/')}/v1/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    ttft_ms: float | None = None
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        for line in resp:
            line_str = line.decode("utf-8").strip()
            if (
                line_str.startswith("data: ")
                and line_str != "data: [DONE]"
                and ttft_ms is None
            ):
                ttft_ms = (time.perf_counter() - start) * 1000.0
    duration_ms = (time.perf_counter() - start) * 1000.0
    if ttft_ms is None:
        ttft_ms = duration_ms
    return ttft_ms, duration_ms


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run cold/warm TTFT warmup experiment")
    parser.add_argument(
        "--worker-urls",
        default="http://127.0.0.1:18001,http://127.0.0.1:18002",
        help="Comma-separated vLLM worker base URLs",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-0.6B",
        help="Model identifier to test",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="Number of warm rounds after initial cold request (total = 1 + rounds)",
    )
    parser.add_argument(
        "--prompt",
        default="Explain key-value cache locality in two sentences.",
        help="Warmup prompt text",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory to write warmup_summary.json",
    )
    args = parser.parse_args(argv)

    urls = [u.strip() for u in args.worker_urls.split(",") if u.strip()]
    results: dict[str, dict] = {}

    for url in urls:
        print(f"== Measuring warmup TTFT for worker at {url} ==")
        samples: list[float] = []
        raw_records = []
        try:
            for i in range(args.rounds + 1):
                label = "unwarmed" if i == 0 else f"warm #{i}"
                ttft, duration = _measure_request_ttft(url, args.model, args.prompt)
                samples.append(ttft)
                raw_records.append({
                    "timestamp": time.time(),
                    "endpoint": url,
                    "model": args.model,
                    "label": label,
                    "ttft_ms": ttft,
                    "duration_ms": duration,
                })
                print(f"  [{label}] TTFT: {ttft:.2f} ms (total: {duration:.2f} ms)")
            summary = aggregate_warmup_ttft(samples)
            summary_dict = asdict(summary)
            summary_dict["unwarmed_request_ttft_ms"] = summary.cold_ttft_ms

            # Attempt to extract true cold-start model load time from pods evidence if available
            pods_file = args.output_dir / "kubectl" / "pods.json" if args.output_dir else None
            if pods_file and pods_file.is_file():
                try:
                    pods_doc = json.loads(pods_file.read_text(encoding="utf-8"))
                    for pod in pods_doc.get("items", []):
                        app_label = pod.get("metadata", {}).get("labels", {}).get("app", "")
                        match_a = "worker-a" in url and app_label == "inference-worker-a"
                        match_b = "worker-b" in url and app_label == "inference-worker-b"
                        if match_a or match_b:
                            statuses = pod.get("status", {}).get("containerStatuses", [])
                            conditions = pod.get("status", {}).get("conditions", [])
                            started_at = (
                                statuses[0].get("state", {}).get("running", {}).get("startedAt")
                                if statuses
                                else None
                            )
                            ready_at = None
                            for cond in conditions:
                                if cond.get("type") == "Ready" and cond.get("status") == "True":
                                    ready_at = cond.get("lastTransitionTime")
                            if started_at and ready_at:
                                from datetime import datetime

                                t_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                                t_ready = datetime.fromisoformat(ready_at.replace("Z", "+00:00"))
                                load_sec = (t_ready - t_start).total_seconds()
                                summary_dict["container_start_to_ready_duration_seconds"] = load_sec
                                print(
                                    f"  Container start to Ready condition: "
                                    f"{load_sec:.1f}s"
                                )
                except Exception:
                    pass

            results[url] = summary_dict
            print(
                f"  Summary for {url}: unwarmed_ttft={summary.cold_ttft_ms:.1f}ms, "
                f"warm_p50={summary.warm_ttft_p50_ms:.1f}ms, "
                f"warm_p95={summary.warm_ttft_p95_ms:.1f}ms"
            )
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            print(
                f"  Worker {url} unreachable or error ({exc}); skipping live measurement."
            )

        if args.output_dir and raw_records:
            raw_dir = args.output_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            with open(raw_dir / "warmup_responses.jsonl", "a", encoding="utf-8") as f:
                for rec in raw_records:
                    f.write(json.dumps(rec) + "\n")
            with open(raw_dir / "responses.jsonl", "a", encoding="utf-8") as f:
                for rec in raw_records:
                    f.write(json.dumps(rec) + "\n")

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_file = args.output_dir / "warmup_summary.json"
        out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Wrote warmup summary to {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

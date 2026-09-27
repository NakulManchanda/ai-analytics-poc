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


def _measure_request_ttft(endpoint: str, model: str, prompt: str) -> float:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "max_tokens": 10,
        "stream": False,
    }).encode("utf-8")
    req = Request(
        f"{endpoint.rstrip('/')}/v1/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        resp.read()
    duration_ms = (time.perf_counter() - start) * 1000.0
    return duration_ms


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
        try:
            for i in range(args.rounds + 1):
                label = "cold" if i == 0 else f"warm #{i}"
                ttft = _measure_request_ttft(url, args.model, args.prompt)
                samples.append(ttft)
                print(f"  [{label}] TTFT: {ttft:.2f} ms")
            summary = aggregate_warmup_ttft(samples)
            results[url] = asdict(summary)
            print(
                f"  Summary for {url}: cold={summary.cold_ttft_ms:.1f}ms, "
                f"warm_p50={summary.warm_ttft_p50_ms:.1f}ms, "
                f"warm_p95={summary.warm_ttft_p95_ms:.1f}ms"
            )
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            print(f"  Worker {url} unreachable or error ({exc}); skipping live measurement.")

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_file = args.output_dir / "warmup_summary.json"
        out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Wrote warmup summary to {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

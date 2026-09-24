#!/usr/bin/env python3
"""Generate randomized burst traffic to the AI Analytics /api/ask endpoint.

Used for load testing, observability dashboards, and trace/metric visualization.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from typing import NamedTuple

QUESTIONS: list[str] = [
    # Top pickup zones
    "Which pickup zones have the most trips?",
    "What are the top 5 busiest pickup locations in New York?",
    "Where do most passengers get picked up in the city?",
    "Show the highest volume taxi pickup locations.",
    # Trip volume by hour
    "What is the trip volume by hour across the city?",
    "Which hour of the day sees the peak taxi volume?",
    "How does trip volume change during morning and evening rush hours?",
    "Show me taxi activity during the late night hours.",
    # Average distance by weekday
    "What is the average trip distance by weekday?",
    "How does travel distance vary on weekdays vs weekends?",
    "What is the average distance traveled on Monday vs Friday?",
    "Are trips longer on weekdays or over the weekend?",
    # Average fare & metrics by borough/region
    "Compare average fare amount across each borough.",
    "What is the average fare and trip distance by region?",
    "Which borough has the highest fare amount per trip?",
    "Show average trip metrics and fare by borough.",
]


class RequestResult(NamedTuple):
    index: int
    prompt: str
    status_code: int
    latency_ms: float
    answer: str
    run_id: str
    error: str | None = None


def send_ask_request(
    url: str, prompt: str, index: int, timeout: float = 30.0
) -> RequestResult:
    payload = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.perf_counter() - start) * 1000.0
            data = json.loads(resp.read().decode("utf-8"))
            answer = data.get("answer", "")
            run_id = data.get("run_id", "")
            return RequestResult(
                index=index,
                prompt=prompt,
                status_code=resp.status,
                latency_ms=latency_ms,
                answer=answer,
                run_id=run_id,
            )
    except urllib.error.HTTPError as err:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(
            index=index,
            prompt=prompt,
            status_code=err.code,
            latency_ms=latency_ms,
            answer="",
            run_id="",
            error=f"HTTP {err.code}: {err.reason}",
        )
    except Exception as err:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(
            index=index,
            prompt=prompt,
            status_code=0,
            latency_ms=latency_ms,
            answer="",
            run_id="",
            error=str(err),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate randomized burst traffic to /api/ask."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:13000",
        help="Base URL of application web frontend (default: http://127.0.0.1:13000)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of requests to send (default: 10)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="Delay in seconds between requests (default: 0.1)",
    )
    args = parser.parse_args()

    count: int = max(1, args.count)
    url: str = args.url
    delay: float = max(0.0, args.delay)

    print(f"🚀 Generating burst traffic: {count} requests -> {url}/api/ask")
    print("-" * 75)

    results: list[RequestResult] = []
    start_total = time.perf_counter()

    for i in range(1, count + 1):
        prompt = random.choice(QUESTIONS)
        result = send_ask_request(url, prompt, i)
        results.append(result)

        if result.error:
            print(
                f"[{i:02d}/{count:02d}] ❌ {result.error} ({result.latency_ms:.1f}ms)"
            )
            print(f'         Prompt: "{prompt}"')
        else:
            short_answer = (
                result.answer[:60] + "..." if len(result.answer) > 60 else result.answer
            )
            print(
                f"[{i:02d}/{count:02d}] ✅ 200 OK ({result.latency_ms:.1f}ms) | {short_answer}"
            )
            print(f'         Prompt: "{prompt}"')

        if i < count and delay > 0:
            time.sleep(delay)

    total_time_s = time.perf_counter() - start_total
    successes = [r for r in results if r.status_code == 200]
    latencies = [r.latency_ms for r in successes]

    print("-" * 75)
    print("📊 Burst Summary:")
    print(f"   Total Requests: {count}")
    print(f"   Successful:     {len(successes)}/{count}")
    print(f"   Elapsed Time:   {total_time_s:.2f}s")
    if latencies:
        avg_lat = sum(latencies) / len(latencies)
        print(
            f"   Latency:        min={min(latencies):.1f}ms | "
            f"avg={avg_lat:.1f}ms | max={max(latencies):.1f}ms"
        )
        print(f"   Throughput:     {len(successes) / total_time_s:.1f} req/s")

    return 0 if len(successes) == count else 1


if __name__ == "__main__":
    sys.exit(main())

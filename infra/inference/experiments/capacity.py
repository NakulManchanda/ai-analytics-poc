"""Capacity calculations and evidence-gated result classification."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_REQUIRED_EVIDENCE = frozenset(
    {"request_results", "vllm_metrics", "dcgm_metrics", "worker_logs"}
)


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def kv_bytes_per_token(
    *,
    num_attention_layers: int,
    num_key_value_heads: int,
    head_dim: int,
    bytes_per_kv_element: int,
) -> int:
    """Return the unrounded K-and-V cache bytes required for one token."""
    return (
        2
        * _positive_int("num_attention_layers", num_attention_layers)
        * _positive_int("num_key_value_heads", num_key_value_heads)
        * _positive_int("head_dim", head_dim)
        * _positive_int("bytes_per_kv_element", bytes_per_kv_element)
    )


def paper_sequence_ceilings(
    *,
    kv_budget_bytes: int,
    kv_bytes_per_token: int,
    context_lengths: tuple[int, ...] | list[int],
) -> dict[int, int]:
    """Calculate whole-sequence KV ceilings for each literal context length."""
    budget = _positive_int("kv_budget_bytes", kv_budget_bytes)
    per_token = _positive_int("kv_bytes_per_token", kv_bytes_per_token)
    if not context_lengths:
        raise ValueError("context_lengths must not be empty")

    ceilings: dict[int, int] = {}
    for context_length in context_lengths:
        length = _positive_int("context_length", context_length)
        ceilings[length] = budget // (per_token * length)
    return ceilings


@dataclass(frozen=True)
class CapacityResult:
    """A first-limiter conclusion accompanied by the artifacts supporting it."""

    first_limiter: str
    classification: str
    evidence_paths: dict[str, str]


def classify_capacity_result(
    *, first_limiter: str, evidence_paths: Mapping[str, str]
) -> CapacityResult:
    """Return an evidenced conclusion, refusing a result based on incomplete artifacts."""
    if not isinstance(first_limiter, str) or not first_limiter.strip():
        raise ValueError("first_limiter must name an observed limiter")
    if not isinstance(evidence_paths, Mapping):
        raise ValueError("evidence_paths must be a mapping")

    missing = _REQUIRED_EVIDENCE.difference(evidence_paths)
    invalid = [
        key
        for key, path in evidence_paths.items()
        if not isinstance(key, str) or not isinstance(path, str) or not path.strip()
    ]
    if missing or invalid:
        details = []
        if missing:
            details.append(f"missing: {', '.join(sorted(missing))}")
        if invalid:
            details.append("empty or invalid paths")
        raise ValueError(
            f"capacity classification requires evidence ({'; '.join(details)})"
        )

    return CapacityResult(
        first_limiter=first_limiter,
        classification="evidenced",
        evidence_paths=dict(evidence_paths),
    )


def _fetch_worker_metrics(endpoint: str) -> str:
    req = Request(f"{endpoint.rstrip('/')}/metrics", method="GET")
    with urlopen(req, timeout=10) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


def run_concurrent_load_step(
    url: str,
    model: str,
    concurrency: int,
    prompt_tokens_target: int,
    max_tokens: int = 15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt = ("NYC taxi ride analytics data " * (max(1, prompt_tokens_target // 5)))[
        : prompt_tokens_target * 4
    ]
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode("utf-8")

    def _single_req(req_idx: int) -> dict[str, Any]:
        start = time.perf_counter()
        req = Request(
            f"{url.rstrip('/')}/v1/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ttft_ms = None
        status = 200
        tokens = 0
        try:
            with urlopen(req, timeout=30) as resp:  # noqa: S310
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if line_str.startswith("data: ") and line_str != "data: [DONE]":
                        if ttft_ms is None:
                            ttft_ms = (time.perf_counter() - start) * 1000.0
                        tokens += 1
        except HTTPError as exc:
            status = exc.code
        except Exception:
            status = 500
        dur_ms = (time.perf_counter() - start) * 1000.0
        return {
            "req_idx": req_idx,
            "status": status,
            "ttft_ms": ttft_ms or dur_ms,
            "duration_ms": dur_ms,
            "tokens": tokens,
        }

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        results = list(executor.map(_single_req, range(concurrency)))
    total_time_s = time.perf_counter() - t0

    ttfts = sorted([r["ttft_ms"] for r in results if r["status"] == 200])
    total_tokens = sum(r["tokens"] for r in results if r["status"] == 200)
    successes = sum(1 for r in results if r["status"] == 200)

    p50_ttft = ttfts[len(ttfts) // 2] if ttfts else None
    p95_ttft = ttfts[int(len(ttfts) * 0.95)] if ttfts else None
    goodput = total_tokens / total_time_s if total_time_s > 0 else 0.0

    step_summary = {
        "concurrency": concurrency,
        "context_length_target": prompt_tokens_target,
        "success_count": successes,
        "error_count": len(results) - successes,
        "ttft_p50_ms": p50_ttft,
        "ttft_p95_ms": p95_ttft,
        "total_duration_seconds": total_time_s,
        "goodput_tokens_per_sec": goodput,
    }
    return results, step_summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run KV capacity and ceilings analysis"
    )
    parser.add_argument(
        "--worker-urls",
        default="http://127.0.0.1:18001,http://127.0.0.1:18002",
        help="Comma-separated vLLM worker base URLs",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-0.6B",
        help="Model name for live capacity queries",
    )
    parser.add_argument(
        "--concurrencies",
        default="1,2,4",
        help="Comma-separated concurrency levels to test",
    )
    parser.add_argument(
        "--layers",
        type=int,
        default=28,
        help="Number of attention layers (default 28 for Qwen3-0.6B)",
    )
    parser.add_argument(
        "--kv-heads",
        type=int,
        default=8,
        help="Number of KV heads (default 8 for Qwen3-0.6B)",
    )
    parser.add_argument(
        "--head-dim",
        type=int,
        default=128,
        help="Head dimension (default 128)",
    )
    parser.add_argument(
        "--kv-bytes",
        type=int,
        default=2,
        help="Bytes per KV element (default 2 for fp16/bf16)",
    )
    parser.add_argument(
        "--kv-budget-mb",
        type=int,
        default=4096,
        help="Estimated KV budget in MiB (default 4096 MiB)",
    )
    parser.add_argument(
        "--context-lengths",
        default="512,2048",
        help="Comma-separated context lengths (e.g. taxi p50, p95, max)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory to write capacity_summary.json",
    )
    args = parser.parse_args(argv)

    per_token = kv_bytes_per_token(
        num_attention_layers=args.layers,
        num_key_value_heads=args.kv_heads,
        head_dim=args.head_dim,
        bytes_per_kv_element=args.kv_bytes,
    )
    context_lengths = [
        int(x.strip()) for x in args.context_lengths.split(",") if x.strip()
    ]
    budget_bytes = args.kv_budget_mb * 1024 * 1024
    ceilings = paper_sequence_ceilings(
        kv_budget_bytes=budget_bytes,
        kv_bytes_per_token=per_token,
        context_lengths=context_lengths,
    )

    print("== Paper KV Capacity Baseline ==")
    print(f"  KV bytes / token: {per_token} bytes")
    print(f"  Assumed KV budget: {args.kv_budget_mb} MiB ({budget_bytes:,} bytes)")
    print("  Paper sequence ceilings:")
    for ctx, max_seq in ceilings.items():
        print(f"    - context {ctx:5d} tokens: {max_seq:4d} concurrent sequences")

    urls = [u.strip() for u in args.worker_urls.split(",") if u.strip()]
    metrics_summary: dict[str, dict] = {}
    for url in urls:
        try:
            raw_metrics = _fetch_worker_metrics(url)
            total_blocks = None
            free_blocks = None
            cache_usage = None
            waiting = None
            for line in raw_metrics.splitlines():
                if line.startswith("vllm:num_total_gpu_blocks"):
                    total_blocks = float(line.split()[-1])
                elif line.startswith("vllm:num_free_gpu_blocks"):
                    free_blocks = float(line.split()[-1])
                elif line.startswith("vllm:kv_cache_usage_perc"):
                    cache_usage = float(line.split()[-1])
                elif line.startswith("vllm:num_requests_waiting"):
                    waiting = float(line.split()[-1])
            metrics_summary[url] = {
                "reachable": True,
                "num_total_gpu_blocks": total_blocks,
                "num_free_gpu_blocks": free_blocks,
                "kv_cache_usage_perc": cache_usage,
                "num_requests_waiting": waiting,
            }
            print(
                f"  Worker {url}: cache_usage={cache_usage}, waiting={waiting}, "
                f"total_blocks={total_blocks}, free_blocks={free_blocks}"
            )
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            metrics_summary[url] = {"reachable": False, "error": str(exc)}
            print(f"  Worker {url}: offline/unreachable ({exc})")

    # Run live capacity sweep against first reachable worker
    live_sweep: list[dict[str, Any]] = []
    all_raw_responses: list[dict[str, Any]] = []
    first_reachable = next((u for u, m in metrics_summary.items() if m.get("reachable")), None)

    observed_limiter = "concurrency_saturation"
    if first_reachable:
        concurrencies = [int(x.strip()) for x in args.concurrencies.split(",") if x.strip()]
        print(f"== Running Live Capacity Sweep against {first_reachable} ==")
        for ctx_len in context_lengths:
            for conc in concurrencies:
                try:
                    req_results, step_summary = run_concurrent_load_step(
                        url=first_reachable,
                        model=args.model,
                        concurrency=conc,
                        prompt_tokens_target=ctx_len,
                    )
                    live_sweep.append(step_summary)
                    all_raw_responses.extend(req_results)
                    succ = step_summary["success_count"]
                    errs = step_summary["error_count"]
                    p50 = step_summary["ttft_p50_ms"]
                    gp = step_summary["goodput_tokens_per_sec"]
                    print(
                        f"  [Context {ctx_len} tokens | Concurrency {conc}] "
                        f"success={succ}, errors={errs}, "
                        f"p50_ttft={p50:.1f}ms, goodput={gp:.1f} tok/s"
                    )
                    if step_summary["error_count"] > 0:
                        observed_limiter = "kv_cache_capacity"
                except Exception as exc:
                    print(f"  [Context {ctx_len} tokens | Concurrency {conc}] Error: {exc}")

    # Determine first limiter
    if any(m.get("kv_cache_usage_perc", 0) or 0 > 0.85 for m in metrics_summary.values()):
        observed_limiter = "kv_cache_capacity"
    elif any(s.get("error_count", 0) > 0 for s in live_sweep):
        observed_limiter = "kv_cache_capacity"
    else:
        observed_limiter = "max_num_seqs_concurrency_limit"

    print(f"== Observed First Limiter: {observed_limiter} ==")

    classification_result = None
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = args.output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        if all_raw_responses:
            with open(raw_dir / "capacity_responses.jsonl", "w", encoding="utf-8") as f:
                for r in all_raw_responses:
                    f.write(json.dumps(r) + "\n")
            with open(raw_dir / "responses.jsonl", "a", encoding="utf-8") as f:
                for r in all_raw_responses:
                    f.write(json.dumps(r) + "\n")

        evidence_paths = {
            "request_results": "raw/responses.jsonl",
            "vllm_metrics": "prometheus/vllm-worker-a.prom",
            "dcgm_metrics": "prometheus/dcgm.prom",
            "worker_logs": "logs/worker-a.log",
        }
        # Check if evidence files exist in output_dir
        if all((args.output_dir / rel).is_file() for rel in evidence_paths.values()):
            try:
                res = classify_capacity_result(
                    first_limiter=observed_limiter, evidence_paths=evidence_paths
                )
                classification_result = {
                    "first_limiter": res.first_limiter,
                    "classification": res.classification,
                    "evidence_paths": res.evidence_paths,
                }
                print(f"Classified capacity result as {res.classification} based on evidence.")
            except Exception as exc:
                print(f"Could not classify capacity result: {exc}")

    out_data = {
        "kv_bytes_per_token": per_token,
        "kv_budget_bytes": budget_bytes,
        "paper_sequence_ceilings": {str(k): v for k, v in ceilings.items()},
        "workers": metrics_summary,
        "live_sweep": live_sweep,
        "first_limiter": observed_limiter,
        "classification": classification_result,
    }
    if args.output_dir:
        out_file = args.output_dir / "capacity_summary.json"
        out_file.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
        print(f"Wrote capacity summary to {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

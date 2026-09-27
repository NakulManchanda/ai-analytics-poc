"""Capacity calculations and evidence-gated result classification."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
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
        default="512,2048,8192",
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
            for line in raw_metrics.splitlines():
                if line.startswith("vllm:num_total_gpu_blocks"):
                    total_blocks = float(line.split()[-1])
                elif line.startswith("vllm:num_free_gpu_blocks"):
                    free_blocks = float(line.split()[-1])
            metrics_summary[url] = {
                "reachable": True,
                "num_total_gpu_blocks": total_blocks,
                "num_free_gpu_blocks": free_blocks,
            }
            print(
                f"  Worker {url}: total_gpu_blocks={total_blocks}, free_gpu_blocks={free_blocks}"
            )
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            metrics_summary[url] = {"reachable": False, "error": str(exc)}
            print(f"  Worker {url}: offline/unreachable ({exc})")

    out_data = {
        "kv_bytes_per_token": per_token,
        "kv_budget_bytes": budget_bytes,
        "paper_sequence_ceilings": {str(k): v for k, v in ceilings.items()},
        "workers": metrics_summary,
    }
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_file = args.output_dir / "capacity_summary.json"
        out_file.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
        print(f"Wrote capacity summary to {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

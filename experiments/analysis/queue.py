"""Part 5 proof: compare gateway queue depth and wait against vLLM waiting/running.

Inputs are pulled Prometheus range artifacts (<run>/prometheus_range/*.json) containing:
- orch_replica_queue_depth
- orch_queue_wait_p95
- vllm_requests_waiting
- vllm_requests_running
- vllm_preemption_rate
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.analysis.memory import load_range

QUEUE_PANELS = (
    "orch_replica_queue_depth",
    "orch_queue_wait_p95",
    "vllm_requests_waiting",
    "vllm_requests_running",
    "vllm_preemption_rate",
)


def _col(name: str, labels: dict[str, str]) -> str:
    parts = [name]
    if "worker" in labels:
        parts.append(labels["worker"])
    elif "instance" in labels:
        parts.append(labels["instance"])
    elif "pod" in labels:
        parts.append(labels["pod"])
    if "class" in labels:
        parts.append(labels["class"])
    return ":".join(parts)


def queue_proof(range_dir: str | Path) -> dict[str, Any]:
    """Join gateway queue and engine scheduler series on a shared time axis."""
    raw = load_range(range_dir)
    warnings = [f"no data for {p}" for p in QUEUE_PANELS if not raw.get(p)]

    cols: dict[str, dict[float, float]] = {}
    for name in QUEUE_PANELS:
        for s in raw.get(name, []):
            cols[_col(name, s["labels"])] = s["points"]

    ts = sorted({t for pts in cols.values() for t in pts})
    series = {c: [pts.get(t) for t in ts] for c, pts in cols.items()}

    summary: dict[str, Any] = {
        "gateway_queue": {},
        "gateway_wait_p95_s": {},
        "vllm_waiting": {},
        "vllm_running": {},
        "vllm_preemptions": {},
    }

    for c, vals in series.items():
        clean = [x for x in vals if x is not None]
        if not clean:
            continue
        peak = max(clean)
        avg = sum(clean) / len(clean)
        parts = c.split(":")
        base = parts[0]
        label = ":".join(parts[1:]) if len(parts) > 1 else "total"

        if base == "orch_replica_queue_depth":
            summary["gateway_queue"][label] = {"peak": peak, "mean": round(avg, 2)}
        elif base == "orch_queue_wait_p95":
            summary["gateway_wait_p95_s"][label] = {"peak": peak, "mean": round(avg, 3)}
        elif base == "vllm_requests_waiting":
            summary["vllm_waiting"][label] = {"peak": peak, "mean": round(avg, 2)}
        elif base == "vllm_requests_running":
            summary["vllm_running"][label] = {"peak": peak, "mean": round(avg, 2)}
        elif base == "vllm_preemption_rate":
            summary["vllm_preemptions"][label] = {"peak": peak, "mean": round(avg, 4)}

    return {
        "timestamps": ts,
        "series": series,
        "summary": summary,
        "warnings": warnings,
    }

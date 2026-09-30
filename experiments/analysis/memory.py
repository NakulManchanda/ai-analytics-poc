"""Memory proof: join HBM, KV, scheduler, rate, preemption and prefix-hit series on one time axis.

Input is the directory written by infra/inference/scripts/pull-prometheus-range.sh
(<run>/prometheus_range/<query_name>.json, each a raw Prometheus query_range response).
Everything here is WINDOW-level time-series evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.analysis.runs import WINDOW

# query name (evidence_queries.json) -> column group
PANELS = (
    "dcgm_fb_used_mib",
    "dcgm_fb_free_mib",
    "vllm_kv_cache_usage",
    "vllm_requests_running",
    "vllm_requests_waiting",
    "vllm_request_rate",
    "vllm_preemption_rate",
    "vllm_prefix_hit_ratio",
)


# HBM counts as 'at max' only at this used/(used+free) level (flat_at_max alone just means flat).
HBM_HIGH_UTIL = 0.90


def load_range(range_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    """{query_name: [{"labels": {...}, "points": {ts: float}}]} (NaN/empty dropped)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(Path(range_dir).glob("*.json")):
        if f.name.startswith("_"):
            continue
        body = json.loads(f.read_text(encoding="utf-8"))
        series = []
        for s in (body.get("data") or {}).get("result", []):
            pts = {
                float(t): float(v)
                for t, v in s.get("values", [])
                if v not in ("NaN", "+Inf", "-Inf")
            }
            if pts:
                series.append({"labels": s.get("metric", {}), "points": pts})
        out[f.stem] = series
    return out


def _col(name: str, labels: dict[str, str], many: bool) -> str:
    if not many:
        return name
    who = labels.get("instance") or labels.get("pod") or labels.get("gpu") or "series"
    return f"{name}:{who}"


def flat_at_max(
    vals: list[float],
    tol: float = 0.02,
    frac: float = 0.8,
    min_level: float | None = None,
) -> bool:
    if (
        len(vals) < 5
        or max(vals) <= 0
        or (min_level is not None and max(vals) < min_level)
    ):
        return False
    top = max(vals)
    return sum(v >= top * (1 - tol) for v in vals) / len(vals) >= frac


def monotonic_growth(vals: list[float], min_rise: float = 0.05) -> bool:
    """Grows by >= min_rise (relative to the peak) and never drops by more than 1 percent of it."""
    if len(vals) < 5 or max(vals) <= 0:
        return False
    peak = max(vals)
    no_drops = all(b >= a - 0.01 * peak for a, b in zip(vals, vals[1:], strict=False))
    return no_drops and (vals[-1] - vals[0]) >= min_rise * peak


def memory_proof(range_dir: str | Path) -> dict[str, Any]:
    raw = load_range(range_dir)
    warnings = [f"no data for {p}" for p in PANELS if not raw.get(p)]
    cols: dict[str, dict[float, float]] = {}
    for name in PANELS:
        for s in raw.get(name, []):
            cols[_col(name, s["labels"], len(raw[name]) > 1)] = s["points"]
    ts = sorted({t for pts in cols.values() for t in pts})
    series = {c: [pts.get(t) for t in ts] for c, pts in cols.items()}
    flags: list[dict[str, str]] = []
    for c, vals in series.items():
        v = [x for x in vals if x is not None]
        base = c.split(":")[0]
        if not v:
            continue
        if base == "vllm_kv_cache_usage":
            if flat_at_max(v, min_level=0.9):
                flags.append(
                    {
                        "series": c,
                        "flag": "flat_at_max",
                        "note": "KV usage pinned near 100 percent",
                    }
                )
            if monotonic_growth(v):
                flags.append(
                    {
                        "series": c,
                        "flag": "monotonic_growth",
                        "note": "KV usage rose without any frees",
                    }
                )
        if base == "dcgm_fb_used_mib":
            if monotonic_growth(v):
                flags.append(
                    {
                        "series": c,
                        "flag": "monotonic_growth",
                        "note": "HBM used rose without any frees",
                    }
                )
            free = series.get(c.replace("dcgm_fb_used_mib", "dcgm_fb_free_mib"))
            util = [
                u / (u + f)
                for u, f in zip(vals, free or [], strict=False)
                if u is not None and f is not None and u + f > 0
            ]
            if util and sum(x >= HBM_HIGH_UTIL for x in util) / len(util) >= 0.8:
                flags.append(
                    {
                        "series": c,
                        "flag": "flat_at_max",
                        "note": f"HBM utilization (used/(used+free)) >= {HBM_HIGH_UTIL} for "
                        "most of the window",
                    }
                )
            elif flat_at_max(v):
                note = (
                    "HBM allocation flat (often vLLM preallocation); not near capacity"
                )
                if not util:
                    note = "HBM flat, capacity unknown (no dcgm_fb_free_mib series)"
                flags.append(
                    {"series": c, "flag": "plateau_or_preallocated", "note": note}
                )
        if base == "vllm_preemption_rate" and max(v) > 0:
            flags.append(
                {
                    "series": c,
                    "flag": "preemptions",
                    "note": "preemptions observed in window",
                }
            )
    running = [
        x
        for c, vals in series.items()
        if c.startswith("vllm_requests_running")
        for x in vals
        if x
    ]
    if cols and not running:
        flags.append(
            {
                "series": "vllm_requests_running",
                "flag": "no_load",
                "note": "no running requests: idle window",
            }
        )
    summary = {
        c: {"min": min(v), "max": max(v), "first": v[0], "last": v[-1]}
        for c, vals in series.items()
        if (v := [x for x in vals if x is not None])
    }
    return {
        "scope": WINDOW,
        "timestamps": ts,
        "series": series,
        "summary": summary,
        "flags": flags,
        "warnings": warnings,
    }

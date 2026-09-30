"""Per-request goodput and run summaries (per-request fields only, never Prometheus)."""

from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Any

from pydantic import BaseModel

from app.benchmarks.replayer import (
    GATEWAY_DECISION_HEADERS,
    TurnResult,
    calculate_percentiles,
)

_PCTS = ("p50", "p95", "p99")


class Slos(BaseModel):
    interactive_ttft_slo_ms: float = 100.0
    default_e2e_slo_ms: float = 3500.0
    # Unmeasured TTFT (e.g. non-streaming gateway) is not good unless disabled.
    require_ttft: bool = True


def is_good(t: TurnResult, slos: Slos) -> bool:
    """Success AND TTFT <= SLO (interactive only) AND e2e <= deadline or default."""
    if t.status != "completed":
        return False
    if t.client_duration_ms > (t.deadline_ms or slos.default_e2e_slo_ms):
        return False
    if (t.workload_class or "interactive") == "interactive" and slos.require_ttft:
        return t.server_ttft_ms is not None and (
            t.server_ttft_ms <= slos.interactive_ttft_slo_ms
        )
    if (t.workload_class or "interactive") == "interactive":
        return t.server_ttft_ms is None or (
            t.server_ttft_ms <= slos.interactive_ttft_slo_ms
        )
    return True


def _rate(turns: list[TurnResult], dur: float) -> float | None:
    """Tokens/s, or None (unavailable) unless usage was measured for every turn."""
    if any(t.tokens_out is None for t in turns):
        return None
    return round(sum(t.tokens_out or 0 for t in turns) / dur, 2)


def _pcts(values: list[float]) -> dict[str, float]:
    full = calculate_percentiles(values)
    return {k: full[k] for k in _PCTS}


def _queue_wait(t: TurnResult) -> float | None:
    try:
        return float((t.gateway_headers or {}).get("x-queue-wait-ms", ""))
    except ValueError:
        return None


def _group(turns: list[TurnResult], key, slos: Slos) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[TurnResult]] = {}
    for t in turns:
        k = key(t)
        if k is not None:
            groups.setdefault(k, []).append(t)
    return {
        k: {
            "requests": len(g),
            "successful": sum(t.status == "completed" for t in g),
            "good_requests": sum(is_good(t, slos) for t in g),
            "e2e_ms": _pcts([t.client_duration_ms for t in g]),
        }
        for k, g in sorted(groups.items())
    }


def placement_reasons_by_turn(turns: list[TurnResult]) -> dict[str, dict[str, int]]:
    """x-placement-reason counts per (1-based) turn (shows the affinity -> spill crossover)."""
    out: dict[str, Counter] = {}
    for t in turns:
        if reason := (t.gateway_headers or {}).get("x-placement-reason"):
            out.setdefault(f"turn_{t.turn_index + 1}", Counter())[reason] += 1
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def summarize_turns(
    turns: list[TurnResult], duration_s: float, slos: Slos
) -> dict[str, Any]:
    dur = max(duration_s, 0.001)
    ok = [t for t in turns if t.status == "completed"]
    good = [t for t in turns if is_good(t, slos)]
    hdr = lambda t, name: (t.gateway_headers or {}).get(name)  # noqa: E731
    decisions = {
        h: dict(Counter(v for t in turns if (v := hdr(t, h)) is not None))
        for h in GATEWAY_DECISION_HEADERS
        if h != "x-queue-wait-ms"
    }
    return {
        "requests": len(turns),
        "successful": len(ok),
        "good_requests": len(good),
        "ttft_unmeasured": sum(t.server_ttft_ms is None for t in ok),
        "duration_s": round(dur, 3),
        "requests_per_s": round(len(turns) / dur, 2),
        "tokens_measured": sum(t.tokens_out is not None for t in ok),
        "tokens_unmeasured": sum(t.tokens_out is None for t in ok),
        "tokens_per_s": _rate(ok, dur),
        "good_requests_per_s": round(len(good) / dur, 2),
        "good_tokens_per_s": _rate(good, dur),
        "ttft_ms": _pcts(
            [t.server_ttft_ms for t in ok if t.server_ttft_ms is not None]
        ),
        "e2e_ms": _pcts([t.client_duration_ms for t in ok]),
        "queue_wait_ms": _pcts([w for t in turns if (w := _queue_wait(t)) is not None]),
        "by_workload_class": _group(turns, lambda t: t.workload_class, slos),
        "by_tenant": _group(turns, lambda t: t.tenant_id, slos),
        "by_worker": _group(turns, lambda t: hdr(t, "x-place-decision"), slos),
        "by_placement_policy": _group(
            turns, lambda t: hdr(t, "x-placement-policy"), slos
        ),
        "decisions": {h: c for h, c in decisions.items() if c},
        "placement_reason_by_turn": placement_reasons_by_turn(turns),
    }


SWEEP_COLUMNS = (
    "offered_concurrency",
    "requests",
    "successful",
    "good_requests",
    "requests_per_s",
    "tokens_measured",
    "tokens_unmeasured",
    "tokens_per_s",
    "good_requests_per_s",
    "good_tokens_per_s",
    "ttft_p95_ms",
    "e2e_p95_ms",
)


def sweep_row(offered_concurrency: int, summary: dict[str, Any]) -> dict[str, Any]:
    row = {"offered_concurrency": offered_concurrency}
    row.update({k: summary[k] for k in SWEEP_COLUMNS[1:10]})
    row["ttft_p95_ms"] = summary["ttft_ms"]["p95"]
    row["e2e_p95_ms"] = summary["e2e_ms"]["p95"]
    return row


def sweep_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows([{k: "n/a" if v is None else v for k, v in r.items()} for r in rows])
    return buf.getvalue()

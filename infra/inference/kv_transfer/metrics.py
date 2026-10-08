"""Bounded-cardinality worker metrics for the Mooncake hop path."""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Histogram

HOP = Counter("hop_total", "KV hop outcomes", ["result", "reason"])
HOP_TOKENS = Counter("hop_tokens_total", "KV hop tokens", ["result"])
HOP_BYTES = Counter("hop_bytes_total", "KV hop bytes", ["result"])
HOP_DURATION = Histogram("hop_duration_seconds", "KV hop duration", ["result"])
HOP_ERROR = Counter("hop_error_total", "KV hop failures", ["reason"])

_RESULTS = {
    "transferred",
    "local_reuse",
    "recomputed",
    "missing",
    "timeout",
    "failed",
}
_REASONS = {
    "local",
    "local_prefix_present",
    "no_prior_worker",
    "transfer_disabled",
    "independently_warmed_destination",
    "remote_prefix_candidate",
    "deadline_recompute",
    "experiment_on",
    "experiment_off",
    "unspecified",
}


def record(event: dict[str, Any]) -> None:
    if event.get("event") == "kv_hop_available":
        return
    result = str(event.get("hop_result", "failed"))
    if result not in _RESULTS:
        result = "failed"
    reason = str(event.get("hop_decision_reason", "unspecified"))
    if reason not in _REASONS:
        reason = "unspecified"
    HOP.labels(result, reason).inc()
    HOP_TOKENS.labels(result).inc(max(0, int(event.get("transferred_tokens", 0))))
    HOP_BYTES.labels(result).inc(max(0, int(event.get("transferred_bytes", 0))))
    duration = max(0.0, float(event.get("transfer_ms", 0.0))) / 1000
    HOP_DURATION.labels(result).observe(duration)
    if result in {"failed", "timeout", "missing"}:
        HOP_ERROR.labels(result).inc()

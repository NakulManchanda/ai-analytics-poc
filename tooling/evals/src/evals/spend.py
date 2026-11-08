"""Reconcile frozen capture model calls against LiteLLM ``/spend/logs`` rows (#226).

Join contract (docs/evals.md): one capture ``call_id`` is one logical model call; LiteLLM
rows carry ``metadata.spend_logs_metadata.{run_id, call_id, attempt}`` and the request tag
``eval_run_id:<id>``. A call can own several rows (policy fallbacks, retries), so spend is
summed per ``call_id``. A tagged row with no capture call is an *orphan*: typically a
request still in flight when its run was cancelled or abandoned. Orphans are real billed
spend the run record never saw, so they are added to the reconciled run cost.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from evals.schema import TraceRecord

TAG_PREFIX = "eval_run_id:"
REL_TOL = 1e-6
ABS_TOL = 1e-9


@dataclass
class CallReconciliation:
    call_id: str
    run_id: str
    capture_cost_usd: float | None
    cost_kind: str | None
    spend_usd: float
    attempts: int
    cost_delta_usd: float | None  # None unless the capture cost is provider-billed


@dataclass
class OrphanRow:
    call_id: str | None
    run_id: str | None
    attempt: int | None
    spend_usd: float
    status: str | None


@dataclass
class SpendReconciliation:
    eval_run_id: str
    calls: list[CallReconciliation] = field(default_factory=list)
    capture_without_spend: list[str] = field(default_factory=list)
    orphans: list[OrphanRow] = field(default_factory=list)
    duplicate_attempts: list[tuple[str, int]] = field(default_factory=list)
    cost_mismatches: list[str] = field(default_factory=list)  # call_ids
    estimated_calls: int = 0
    rows_considered: int = 0

    @property
    def capture_cost_usd(self) -> float:
        return sum(c.capture_cost_usd or 0.0 for c in self.calls)

    @property
    def matched_spend_usd(self) -> float:
        return sum(c.spend_usd for c in self.calls)

    @property
    def orphan_spend_usd(self) -> float:
        return sum(o.spend_usd for o in self.orphans)

    @property
    def reconciled_cost_usd(self) -> float:
        """What the provider billed for this eval run: matched spend plus orphans."""
        return self.matched_spend_usd + self.orphan_spend_usd

    @property
    def violations(self) -> list[str]:
        out = [f"capture call {c} has no spend row" for c in self.capture_without_spend]
        out += [
            f"duplicate spend row for {c} attempt {a}"
            for c, a in self.duplicate_attempts
        ]
        out += [
            f"billed cost differs from spend for call {c}" for c in self.cost_mismatches
        ]
        return out

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_json(self) -> dict[str, Any]:
        return {
            "eval_run_id": self.eval_run_id,
            "ok": self.ok,
            "violations": self.violations,
            "rows_considered": self.rows_considered,
            "capture_calls": len(self.calls) + len(self.capture_without_spend),
            "estimated_calls": self.estimated_calls,
            "capture_cost_usd": self.capture_cost_usd,
            "matched_spend_usd": self.matched_spend_usd,
            "orphan_spend_usd": self.orphan_spend_usd,
            "reconciled_cost_usd": self.reconciled_cost_usd,
            "orphans": [asdict(o) for o in self.orphans],
            "capture_without_spend": self.capture_without_spend,
            "duplicate_attempts": [list(d) for d in self.duplicate_attempts],
            "calls": [asdict(c) for c in self.calls],
        }


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata") or {}
    return meta.get("spend_logs_metadata") or meta


def _tags(row: dict[str, Any]) -> list[str]:
    tags = row.get("request_tags") or (row.get("metadata") or {}).get("request_tags")
    return [t for t in (tags or []) if isinstance(t, str)]


def tagged_rows(rows: list[dict[str, Any]], eval_run_id: str) -> list[dict[str, Any]]:
    """Only rows tagged for this eval run; untagged traffic is never counted."""
    want = f"{TAG_PREFIX}{eval_run_id}"
    return [r for r in rows if want in _tags(r)]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reconcile(
    records: list[TraceRecord], spend_rows: list[dict[str, Any]], eval_run_id: str
) -> SpendReconciliation:
    rows = tagged_rows(spend_rows, eval_run_id)
    result = SpendReconciliation(eval_run_id=eval_run_id, rows_considered=len(rows))
    calls = {r.call_id: r for r in records if r.record_type == "model_call"}

    by_call: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        meta = _meta(row)
        call_id = meta.get("call_id")
        if call_id in calls:
            by_call.setdefault(call_id, []).append(row)
        else:
            result.orphans.append(
                OrphanRow(
                    call_id=call_id,
                    run_id=meta.get("run_id"),
                    attempt=_as_int(meta.get("attempt")),
                    spend_usd=float(row.get("spend") or 0.0),
                    status=row.get("status"),
                )
            )

    for call_id, rec in calls.items():
        matched = by_call.get(call_id)
        if not matched:
            result.capture_without_spend.append(call_id)
            continue
        seen: set[int] = set()
        for row in matched:
            attempt = _as_int(_meta(row).get("attempt"))
            if attempt is None:
                continue
            if attempt in seen:
                result.duplicate_attempts.append((call_id, attempt))
            seen.add(attempt)
        spend = sum(float(r.get("spend") or 0.0) for r in matched)
        delta = None
        if rec.cost_kind == "billed" and rec.cost_usd is not None:
            delta = rec.cost_usd - spend
            if not math.isclose(rec.cost_usd, spend, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                result.cost_mismatches.append(call_id)
        elif rec.cost_kind != "billed":
            result.estimated_calls += 1
        result.calls.append(
            CallReconciliation(
                call_id=call_id,
                run_id=rec.run_id,
                capture_cost_usd=rec.cost_usd,
                cost_kind=rec.cost_kind,
                spend_usd=spend,
                attempts=len(matched),
                cost_delta_usd=delta,
            )
        )
    return result

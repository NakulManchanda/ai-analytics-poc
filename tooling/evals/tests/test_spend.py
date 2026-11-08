from __future__ import annotations

import pytest

from evals.schema import TraceRecord
from evals.spend import reconcile, tagged_rows

RUN = "eval-1"


def _call(
    call_id: str, cost: float | None = 0.01, kind: str | None = "billed"
) -> TraceRecord:
    return TraceRecord(
        record_type="model_call",
        run_id="run_1",
        turn_id="t1",
        step=1,
        call_id=call_id,
        cost_usd=cost,
        cost_kind=kind,
    )


def _row(
    call_id: str | None,
    spend: float = 0.01,
    attempt: int = 1,
    tags: list[str] | None = None,
    status: str = "success",
    run_id: str = "run_1",
) -> dict:
    return {
        "spend": spend,
        "status": status,
        "request_tags": [f"eval_run_id:{RUN}"] if tags is None else tags,
        "metadata": {
            "spend_logs_metadata": {
                "call_id": call_id,
                "attempt": attempt,
                "run_id": run_id,
            }
        },
    }


def test_clean_run_is_ok_and_reconciled_cost_equals_capture() -> None:
    result = reconcile(
        [_call("c1"), _call("c2", 0.02)], [_row("c1"), _row("c2", 0.02)], RUN
    )
    assert result.ok and not result.orphans
    assert result.reconciled_cost_usd == pytest.approx(0.03)
    assert result.to_json()["capture_calls"] == 2


def test_fallback_sums_rows_per_call() -> None:
    result = reconcile(
        [_call("c1", 0.03)],
        [_row("c1", 0.01, attempt=1, status="failure"), _row("c1", 0.02, attempt=2)],
        RUN,
    )
    assert result.ok
    assert result.calls[0].attempts == 2
    assert result.calls[0].spend_usd == pytest.approx(0.03)


def test_cancelled_in_flight_request_is_an_orphan_added_to_run_cost() -> None:
    result = reconcile(
        [_call("c1")], [_row("c1"), _row("c_cancelled", 0.05, run_id="run_2")], RUN
    )
    assert result.ok  # orphans are reported, not a failed invariant
    assert [o.call_id for o in result.orphans] == ["c_cancelled"]
    assert result.orphan_spend_usd == pytest.approx(0.05)
    assert result.reconciled_cost_usd == pytest.approx(0.06)
    assert result.capture_cost_usd == pytest.approx(0.01)


def test_row_without_call_metadata_is_an_orphan() -> None:
    result = reconcile([_call("c1")], [_row("c1"), _row(None, 0.02)], RUN)
    assert result.orphans[0].call_id is None and result.orphan_spend_usd == 0.02


def test_capture_call_without_spend_row_is_a_violation() -> None:
    result = reconcile([_call("c1"), _call("c2")], [_row("c1")], RUN)
    assert not result.ok
    assert result.capture_without_spend == ["c2"]


def test_duplicate_attempt_is_a_violation() -> None:
    result = reconcile([_call("c1", 0.02)], [_row("c1"), _row("c1")], RUN)
    assert result.duplicate_attempts == [("c1", 1)]
    assert not result.ok


def test_billed_cost_mismatch_is_a_violation_estimated_is_not() -> None:
    billed = reconcile([_call("c1", 0.01)], [_row("c1", 0.02)], RUN)
    assert billed.cost_mismatches == ["c1"] and not billed.ok
    estimated = reconcile([_call("c1", 0.01, "estimated")], [_row("c1", 0.02)], RUN)
    assert estimated.ok and estimated.estimated_calls == 1
    assert estimated.calls[0].cost_delta_usd is None


def test_untagged_and_other_run_rows_are_never_counted() -> None:
    rows = [
        _row("c1"),
        _row("x", 9.0, tags=[]),
        _row("y", 9.0, tags=["eval_run_id:other"]),
    ]
    assert len(tagged_rows(rows, RUN)) == 1
    result = reconcile([_call("c1")], rows, RUN)
    assert result.ok and result.reconciled_cost_usd == 0.01

from __future__ import annotations

import time

from app.orchestration.budgets import BudgetTracker, ExecutionBudgets
from app.validation import retry_allowed, validate_proposal


def tracker(**kw) -> BudgetTracker:  # type: ignore[no-untyped-def]
    budgets = kw.pop("budgets", ExecutionBudgets())
    t = BudgetTracker(budgets=budgets)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def recoverable():  # type: ignore[no-untyped-def]
    return validate_proposal("query_taxi_data", {"analysis": "x", "limit": 5})


def test_allowed_with_headroom() -> None:
    assert retry_allowed(
        recoverable(), tracker(llm_call_count=1), 10, 5, retries_used=0
    )


def test_not_allowed_after_first_retry() -> None:
    assert not retry_allowed(
        recoverable(), tracker(llm_call_count=1), 10, 5, retries_used=1
    )


def test_not_allowed_for_terminal_violation() -> None:
    terminal = validate_proposal(
        "query_taxi_data",
        {"analysis": "top_pickup_zones", "limit": 5},
        governed_calls_executed=1,
    )
    assert not retry_allowed(terminal, tracker(), 10, 5, retries_used=0)


def test_not_allowed_without_time_headroom() -> None:
    t = tracker(budgets=ExecutionBudgets(timeout_seconds=10.0))
    t.start_time = time.monotonic() - 6.0  # more than half the deadline used
    assert not retry_allowed(recoverable(), t, 10, 5, retries_used=0)


def test_not_allowed_without_llm_call_headroom() -> None:
    t = tracker(budgets=ExecutionBudgets(max_llm_calls=2), llm_call_count=1)
    assert not retry_allowed(recoverable(), t, 10, 5, retries_used=0)


def test_denied_when_retry_fits_but_retry_plus_answer_does_not() -> None:
    t = tracker(
        budgets=ExecutionBudgets(max_input_tokens=25, max_output_tokens=100),
        llm_call_count=1,
        input_tokens=10,
        output_tokens=5,
    )
    # one more call (10) would fit under 25, but retry + answer (20) would not
    assert not retry_allowed(recoverable(), t, 10, 5, retries_used=0)

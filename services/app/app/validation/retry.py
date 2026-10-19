"""Policy for the single bounded validate -> retry-with-feedback step.

A retry is a *second proposal call*; it never executes a tool, so it cannot
replay a side effect. It is allowed only when every violation is a
recoverable shape/argument error and the run budgets can still pay for the
retry AND the final answer. Auth/tenant denials arrive from MCP after
execution and deadline/budget errors are raised by the tracker; neither
passes through here.
"""

from __future__ import annotations

from app.orchestration.budgets import BudgetTracker
from app.validation.proposal import ProposalValidation

MAX_VALIDATION_RETRIES = 1
# A retry must start while at most this share of the wall-clock deadline is used.
MAX_ELAPSED_FRACTION = 0.5


def retry_allowed(
    validation: ProposalValidation,
    tracker: BudgetTracker,
    last_input_tokens: int,
    last_output_tokens: int,
    *,
    retries_used: int,
) -> bool:
    if retries_used >= MAX_VALIDATION_RETRIES or not validation.recoverable:
        return False
    budgets = tracker.budgets
    # retry proposal + final answer must both fit in the LLM-call budget
    if tracker.llm_call_count + 2 > budgets.max_llm_calls:
        return False
    if tracker.elapsed_seconds > budgets.timeout_seconds * MAX_ELAPSED_FRACTION:
        return False
    if tracker.input_tokens + last_input_tokens > budgets.max_input_tokens:
        return False
    if tracker.output_tokens + last_output_tokens > budgets.max_output_tokens:
        return False
    return True

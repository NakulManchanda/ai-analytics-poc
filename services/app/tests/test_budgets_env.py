"""AGENT_* environment overrides for the per-run execution budgets."""

from __future__ import annotations

import pytest
from app.llm import LocalFakeLLMClient
from app.orchestration.budgets import ExecutionBudgets
from app.orchestration.loop import OrchestrationLoop
from app.state.repository import InMemoryStateRepository
from tests.test_orchestration_loop import FakeMCPClient

VARS = (
    "AGENT_MAX_INPUT_TOKENS",
    "AGENT_MAX_TOOL_CALLS",
    "AGENT_MAX_LLM_CALLS",
    "AGENT_MAX_ITERATIONS",
    "AGENT_TIMEOUT_SECONDS",
)


def test_no_overrides_keeps_the_defaults() -> None:
    assert ExecutionBudgets.from_env({}) == ExecutionBudgets()


def test_blank_values_are_unset() -> None:
    assert ExecutionBudgets.from_env({v: "  " for v in VARS}) == ExecutionBudgets()


def test_overrides_are_applied_and_others_untouched() -> None:
    budgets = ExecutionBudgets.from_env(
        {"AGENT_MAX_INPUT_TOKENS": "40000", "AGENT_MAX_TOOL_CALLS": "8"}
    )
    assert budgets.max_input_tokens == 40_000
    assert budgets.max_tool_calls == 8
    assert budgets.max_llm_calls == ExecutionBudgets().max_llm_calls
    assert budgets.max_estimated_cost_usd == ExecutionBudgets().max_estimated_cost_usd


def test_timeout_accepts_a_float() -> None:
    assert (
        ExecutionBudgets.from_env({"AGENT_TIMEOUT_SECONDS": "90.5"}).timeout_seconds
        == 90.5
    )


@pytest.mark.parametrize("bad", ["abc", "1.5", "0", "-3", "nan"])
def test_invalid_value_fails_loudly_and_names_the_variable(bad: str) -> None:
    with pytest.raises(ValueError, match="AGENT_MAX_INPUT_TOKENS"):
        ExecutionBudgets.from_env({"AGENT_MAX_INPUT_TOKENS": bad})


def test_loop_reads_the_environment_but_explicit_budgets_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MAX_INPUT_TOKENS", "40000")

    def make(budgets: ExecutionBudgets | None) -> OrchestrationLoop:
        return OrchestrationLoop(
            llm_client=LocalFakeLLMClient(),
            mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
            state_repository=InMemoryStateRepository(),
            budgets=budgets,
        )

    assert make(None)._budgets.max_input_tokens == 40_000
    explicit = ExecutionBudgets(max_input_tokens=1_000)
    assert make(explicit)._budgets.max_input_tokens == 1_000

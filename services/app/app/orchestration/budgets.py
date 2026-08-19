from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceededError(Exception):
    """Raised when any execution budget or resource limit is breached."""

    def __init__(self, reason: str, details: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


@dataclass(frozen=True)
class ExecutionBudgets:
    """Configured hard resource limits for an agent orchestration run."""

    max_iterations: int = 5
    max_llm_calls: int = 6
    max_tool_calls: int = 5
    timeout_seconds: float = 30.0
    max_input_tokens: int = 20_000
    max_output_tokens: int = 4_000
    max_estimated_cost_usd: float = 0.50
    max_tool_result_bytes: int = 32_768  # 32 KiB


@dataclass
class BudgetTracker:
    """Tracks runtime resource consumption and validates against ExecutionBudgets."""

    budgets: ExecutionBudgets = field(default_factory=ExecutionBudgets)
    start_time: float = field(default_factory=time.monotonic)
    iteration_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record_iteration(self) -> None:
        self.iteration_count += 1
        if self.iteration_count > self.budgets.max_iterations:
            raise BudgetExceededError(
                f"Exceeded max iterations limit of {self.budgets.max_iterations}",
                {"limit": "max_iterations", "current": self.iteration_count},
            )
        self.check_deadline()

    def check_deadline(self) -> None:
        if self.elapsed_seconds > self.budgets.timeout_seconds:
            raise BudgetExceededError(
                f"Exceeded execution timeout limit of {self.budgets.timeout_seconds}s",
                {"limit": "timeout_seconds", "elapsed": self.elapsed_seconds},
            )

    def record_llm_call(
        self,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        self.llm_call_count += 1
        if self.llm_call_count > self.budgets.max_llm_calls:
            raise BudgetExceededError(
                f"Exceeded max LLM calls limit of {self.budgets.max_llm_calls}",
                {"limit": "max_llm_calls", "current": self.llm_call_count},
            )
        self.input_tokens += input_tokens
        if self.input_tokens > self.budgets.max_input_tokens:
            raise BudgetExceededError(
                f"Exceeded max input tokens limit of {self.budgets.max_input_tokens}",
                {"limit": "max_input_tokens", "current": self.input_tokens},
            )
        self.output_tokens += output_tokens
        if self.output_tokens > self.budgets.max_output_tokens:
            raise BudgetExceededError(
                f"Exceeded max output tokens limit of {self.budgets.max_output_tokens}",
                {"limit": "max_output_tokens", "current": self.output_tokens},
            )
        self.estimated_cost_usd += estimated_cost_usd
        if self.estimated_cost_usd > self.budgets.max_estimated_cost_usd:
            raise BudgetExceededError(
                f"Exceeded max estimated cost limit of ${self.budgets.max_estimated_cost_usd:.4f}",
                {
                    "limit": "max_estimated_cost_usd",
                    "current": self.estimated_cost_usd,
                },
            )
        self.check_deadline()

    def record_tool_call(self, result_bytes: int = 0) -> None:
        self.tool_call_count += 1
        if self.tool_call_count > self.budgets.max_tool_calls:
            raise BudgetExceededError(
                f"Exceeded max tool calls limit of {self.budgets.max_tool_calls}",
                {"limit": "max_tool_calls", "current": self.tool_call_count},
            )
        if result_bytes > self.budgets.max_tool_result_bytes:
            raise BudgetExceededError(
                f"Exceeded max tool result bytes limit ({self.budgets.max_tool_result_bytes})",
                {"limit": "max_tool_result_bytes", "current": result_bytes},
            )
        self.check_deadline()

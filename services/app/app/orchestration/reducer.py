from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.orchestration.budgets import BudgetTracker, ExecutionBudgets
from app.state import Message

DEFAULT_RECENT_TURNS_WINDOW = 2  # Keep last 2 user-assistant turns (up to 4 messages)
MAX_PREVIEW_ROWS = 3


@dataclass(frozen=True)
class WorkingContext:
    """Bounded working context exposing stored vs. included divergence and execution state."""

    conversation_summary: str | None
    current_user_message: str
    recent_messages: list[dict[str, Any]]
    available_tools: list[str]
    dataset_schema: dict[str, Any]
    recent_tool_observations: list[dict[str, Any]]
    assumptions: list[str]
    artifacts: list[str]
    failures: list[str]
    remaining_budget: dict[str, Any]
    stored_message_count: int
    included_message_count: int
    schema_size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_summary": self.conversation_summary,
            "current_user_message": self.current_user_message,
            "recent_messages": self.recent_messages,
            "available_tools": self.available_tools,
            "dataset_schema": self.dataset_schema,
            "recent_tool_observations": self.recent_tool_observations,
            "assumptions": self.assumptions,
            "artifacts": self.artifacts,
            "failures": self.failures,
            "remaining_budget": self.remaining_budget,
            "stored_message_count": self.stored_message_count,
            "included_message_count": self.included_message_count,
            "schema_size_bytes": self.schema_size_bytes,
        }


def summarize_messages(messages: list[Message]) -> str:
    """Deterministically summarize older conversation turns into a bounded string."""
    if not messages:
        return ""
    summaries: list[str] = []
    for msg in messages:
        content_preview = msg.content.strip().replace("\n", " ")
        if len(content_preview) > 60:
            content_preview = content_preview[:57] + "..."
        summaries.append(f"{msg.role}: {content_preview}")
    return " | ".join(summaries)


def sanitize_and_preview_tool_result(
    tool_result: dict[str, Any], max_rows: int = MAX_PREVIEW_ROWS
) -> dict[str, Any]:
    """Reduce large tool results to schema + aggregates + preview + artifact ref."""
    rows = tool_result.get("rows", [])
    columns = tool_result.get("columns", [])
    row_count = tool_result.get("row_count", len(rows))
    query_id = str(tool_result.get("query_id", "unknown"))

    preview_rows = rows[:max_rows] if isinstance(rows, list) else []
    artifact_ref = f"artifact://nyc-taxi/queries/{query_id}"

    return {
        "query_id": query_id,
        "columns": columns,
        "row_count": row_count,
        "preview_rows": preview_rows,
        "artifact_ref": artifact_ref,
        "execution_duration_ms": tool_result.get("execution_duration_ms", 0),
    }


class ContextReducer:
    """Deterministic context reducer ensuring durable state != working LLM context."""

    def __init__(self, recent_turns_window: int = DEFAULT_RECENT_TURNS_WINDOW) -> None:
        self.recent_turns_window = recent_turns_window

    def reduce(
        self,
        current_prompt: str,
        stored_messages: list[Message],
        dataset_schema: dict[str, Any] | None = None,
        tool_observations: list[dict[str, Any]] | None = None,
        available_tools: list[str] | None = None,
        failures: list[str] | None = None,
        assumptions: list[str] | None = None,
        budget_tracker: BudgetTracker | None = None,
        budgets: ExecutionBudgets | None = None,
    ) -> WorkingContext:
        # 1. Total stored messages count (including current prompt if stored)
        stored_count = len(stored_messages)

        # 2. Separate recent messages vs older messages to summarize
        # Recent window: max_recent_messages = recent_turns_window * 2 (user + assistant)
        max_recent_messages = self.recent_turns_window * 2

        # Filter out the current user message if it was already added to stored_messages at the end
        historical_messages = [
            m
            for m in stored_messages
            if m.content != current_prompt or m.role != "user"
        ]

        if len(historical_messages) > max_recent_messages:
            older_messages = historical_messages[:-max_recent_messages]
            recent_stored = historical_messages[-max_recent_messages:]
            conversation_summary = summarize_messages(older_messages)
        else:
            recent_stored = historical_messages
            conversation_summary = None

        recent_formatted = [
            {
                "message_id": m.message_id,
                "role": m.role,
                "content": m.content,
                "sequence": m.sequence,
            }
            for m in recent_stored
        ]

        # Included messages count: recent messages + current prompt
        included_count = len(recent_stored) + 1

        # 3. Deduplicate and compute schema size
        schema = dataset_schema or {}
        schema_bytes = len(json.dumps(schema, separators=(",", ":")).encode("utf-8"))

        # 4. Reduce tool observations with preview + aggregates + artifact ref
        reduced_observations: list[dict[str, Any]] = []
        artifact_refs: list[str] = []
        if tool_observations:
            for obs in tool_observations:
                reduced_obs = sanitize_and_preview_tool_result(obs)
                reduced_observations.append(reduced_obs)
                if reduced_obs.get("artifact_ref"):
                    artifact_refs.append(reduced_obs["artifact_ref"])

        # 5. Remaining budgets computation
        active_budgets = budgets or ExecutionBudgets()
        if budget_tracker is not None:
            remaining_iterations = max(
                0, active_budgets.max_iterations - budget_tracker.iteration_count
            )
            remaining_tools = max(
                0, active_budgets.max_tool_calls - budget_tracker.tool_call_count
            )
            remaining_llm = max(
                0, active_budgets.max_llm_calls - budget_tracker.llm_call_count
            )
            remaining_tokens = max(
                0, active_budgets.max_input_tokens - budget_tracker.input_tokens
            )
            remaining_cost = max(
                0.0,
                active_budgets.max_estimated_cost_usd
                - budget_tracker.estimated_cost_usd,
            )
            current_iter = budget_tracker.iteration_count
        else:
            remaining_iterations = active_budgets.max_iterations
            remaining_tools = active_budgets.max_tool_calls
            remaining_llm = active_budgets.max_llm_calls
            remaining_tokens = active_budgets.max_input_tokens
            remaining_cost = active_budgets.max_estimated_cost_usd
            current_iter = 1

        remaining_budget_dict = {
            "current_iteration": current_iter,
            "max_iterations": active_budgets.max_iterations,
            "remaining_iterations": remaining_iterations,
            "remaining_tool_calls": remaining_tools,
            "remaining_llm_calls": remaining_llm,
            "remaining_input_tokens": remaining_tokens,
            "remaining_estimated_cost_usd": round(remaining_cost, 6),
            "max_tool_calls": active_budgets.max_tool_calls,
            "max_llm_calls": active_budgets.max_llm_calls,
            "max_input_tokens": active_budgets.max_input_tokens,
            "max_estimated_cost_usd": active_budgets.max_estimated_cost_usd,
        }

        tools_list = available_tools or ["query_taxi_data"]

        return WorkingContext(
            conversation_summary=conversation_summary,
            current_user_message=current_prompt,
            recent_messages=recent_formatted,
            available_tools=tools_list,
            dataset_schema=schema,
            recent_tool_observations=reduced_observations,
            assumptions=assumptions or [],
            artifacts=artifact_refs,
            failures=failures or [],
            remaining_budget=remaining_budget_dict,
            stored_message_count=stored_count,
            included_message_count=included_count,
            schema_size_bytes=schema_bytes,
        )

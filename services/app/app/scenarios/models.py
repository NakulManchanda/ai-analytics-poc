from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScenarioTurn(BaseModel):
    """A single turn within a conversation."""

    question: str = Field(..., min_length=1, description="Analytical query text")
    expected_tool: str | None = Field(
        default=None, description="Expected governed MCP tool name if catalogue-matched"
    )
    delay_seconds: float = Field(
        default=0.0, ge=0.0, description="Optional delay before submitting this turn"
    )


class ScenarioConversation(BaseModel):
    """A sequential series of turns belonging to a distinct conversation."""

    conversation_id_prefix: str = Field(
        default="conv", description="Prefix for generated conversation IDs"
    )
    turns: list[ScenarioTurn] = Field(
        ..., min_length=1, description="Sequential turns in this conversation"
    )


class ScenarioConfig(BaseModel):
    """Declarative specification for a replayable benchmark scenario."""

    name: str = Field(..., min_length=1, description="Unique scenario identifier")
    description: str = Field(..., description="Human-readable scenario description")
    concurrency: int = Field(
        default=1, ge=1, le=50, description="Max concurrent active conversations"
    )
    strategy: Literal["manual", "crewai"] = Field(
        default="manual", description="Agent execution strategy"
    )
    target_endpoint_type: Literal["app_runs", "gateway_chat"] = Field(
        default="app_runs", description="Target endpoint type"
    )
    conversations: list[ScenarioConversation] = Field(
        ..., min_length=1, description="List of conversations to execute"
    )

    @property
    def total_turns(self) -> int:
        return sum(len(c.turns) for c in self.conversations)

    @property
    def total_conversations(self) -> int:
        return len(self.conversations)

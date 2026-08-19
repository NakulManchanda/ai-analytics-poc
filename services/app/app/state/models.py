from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utcnow_isoformat() -> str:
    """Return the current UTC timestamp formatted as ISO 8601."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Conversation:
    """Represents a durable conversation container."""

    conversation_id: str
    created_at: str = field(default_factory=utcnow_isoformat)
    updated_at: str = field(default_factory=utcnow_isoformat)
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    """Represents a durable conversational message/turn."""

    message_id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    created_at: str = field(default_factory=utcnow_isoformat)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Run:
    """Represents a durable execution run for an orchestration cycle."""

    run_id: str
    conversation_id: str
    message_id: str | None = None
    status: str = "in_progress"
    model: str | None = None
    prompt_version: str | None = None
    started_at: str = field(default_factory=utcnow_isoformat)
    completed_at: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    failure_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunStep:
    """Represents an individual step executed within an orchestration run."""

    step_id: str
    run_id: str
    sequence: int
    step_type: str
    status: str = "in_progress"
    tool_name: str | None = None
    llm_call_id: str | None = None
    tool_call_id: str | None = None
    query_id: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    started_at: str = field(default_factory=utcnow_isoformat)
    completed_at: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    """Represents a durable asynchronous execution job."""

    job_id: str
    job_type: str = "create_full_report"
    status: str = "PENDING"
    created_at: str = field(default_factory=utcnow_isoformat)
    started_at: str | None = None
    completed_at: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    artifact_url: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

from __future__ import annotations

import uuid


def generate_conversation_id() -> str:
    """Return a unique identifier for a conversation."""
    return f"conv_{uuid.uuid4().hex[:16]}"


def generate_message_id() -> str:
    """Return a unique identifier for a message."""
    return f"msg_{uuid.uuid4().hex[:16]}"


def generate_run_id() -> str:
    """Return a unique identifier for an orchestration run."""
    return f"run_{uuid.uuid4().hex[:16]}"


def generate_step_id() -> str:
    """Return a unique identifier for a run step."""
    return f"step_{uuid.uuid4().hex[:16]}"


def generate_llm_call_id() -> str:
    """Return a unique identifier for an individual LLM invocation."""
    return f"call_{uuid.uuid4().hex[:16]}"


def generate_tool_call_id() -> str:
    """Return a unique identifier for an individual MCP tool invocation."""
    return f"tcall_{uuid.uuid4().hex[:16]}"


def generate_query_id() -> str:
    """Return a unique identifier for an individual DuckDB query execution."""
    return f"qry_{uuid.uuid4().hex[:16]}"

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def utcnow_isoformat() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RunEvent:
    """Represents a bounded, public, versioned execution event streamed to clients."""

    event_type: str
    run_id: str
    conversation_id: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:16]}")
    step_id: str | None = None
    llm_call_id: str | None = None
    tool_call_id: str | None = None
    query_id: str | None = None
    timestamp: str = field(default_factory=utcnow_isoformat)

    def to_dict(self) -> dict[str, str]:
        """Serialize for Redis stream storage (string key-value pairs)."""
        data: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "sequence": str(self.sequence),
            "timestamp": self.timestamp,
            "payload": json.dumps(self.payload),
        }
        if self.step_id:
            data["step_id"] = self.step_id
        if self.llm_call_id:
            data["llm_call_id"] = self.llm_call_id
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.query_id:
            data["query_id"] = self.query_id
        return {k: str(v) for k, v in data.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunEvent:
        """Deserialize from Redis stream or DynamoDB snapshot dictionary."""
        payload_raw = data.get("payload", "{}")
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = {}
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        else:
            payload = {}

        return cls(
            event_id=str(data.get("event_id", "")),
            event_type=str(data.get("event_type", "")),
            run_id=str(data.get("run_id", "")),
            conversation_id=str(data.get("conversation_id", "")),
            sequence=int(data.get("sequence", 0)),
            step_id=data.get("step_id"),
            llm_call_id=data.get("llm_call_id"),
            tool_call_id=data.get("tool_call_id"),
            query_id=data.get("query_id"),
            payload=payload,
            timestamp=str(data.get("timestamp", "")),
        )

    def to_sse(self) -> str:
        """Format as Server-Sent Event frame."""
        data_json = json.dumps(
            {
                "event_id": self.event_id,
                "event_type": self.event_type,
                "run_id": self.run_id,
                "conversation_id": self.conversation_id,
                "sequence": self.sequence,
                "step_id": self.step_id,
                "llm_call_id": self.llm_call_id,
                "tool_call_id": self.tool_call_id,
                "query_id": self.query_id,
                "timestamp": self.timestamp,
                "payload": self.payload,
            }
        )
        return f"id: {self.event_id}\nevent: {self.event_type}\ndata: {data_json}\n\n"

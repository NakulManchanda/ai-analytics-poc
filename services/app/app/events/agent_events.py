"""Versioned structured agent/subagent streaming event contract (#232, Epic #231).

Provides truthful lifecycle, tool, and artifact events independent of any agent framework,
transported over the existing `RunEvent` / Redis Streams / SSE pipeline without adding a
parallel serving stack. Enforces bounded payloads, tenant isolation, sequence-based
deduplication on reconnect, and privacy rules (no chain-of-thought, secrets, or fabricated
progress percentages).
"""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.events.models import RunEvent, utcnow_isoformat
from app.events.publisher import EventPublisher

AGENT_EVENT_SCHEMA_VERSION = "agent_event.v1"
MAX_EVENT_PAYLOAD_BYTES = 16 * 1024

AgentEventType = Literal[
    "agent.started",
    "agent.progress",
    "agent.completed",
    "agent.failed",
    "agent.cancelled",
    "tool.started",
    "tool.finished",
    "artifact.ready",
]

AgentLifecycleStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

_FORBIDDEN_KEYS = frozenset(
    {
        "chain_of_thought",
        "cot",
        "hidden_reasoning",
        "internal_monologue",
        "reasoning_trace",
        "thinking",
        "progress_percent",
        "percent_complete",
        "api_key",
        "authorization",
        "aws_secret_access_key",
        "aws_session_token",
        "secret",
        "password",
        "private_key",
    }
)
_SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._\-]{16,})",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class AgentEventError(ValueError):
    """Raised when an event violates schema, privacy, size, or tenant boundaries."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _clean_id(value: str) -> str:
    if _CONTROL_RE.search(value):
        raise ValueError("control characters not allowed")
    return value


def _finite_nonneg(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        raise ValueError("must be finite and non-negative")
    return value


CleanId = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(_clean_id)]


def _scan_payload(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_low = str(k).lower()
            if key_low in _FORBIDDEN_KEYS:
                raise AgentEventError(
                    "forbidden_field",
                    f"forbidden field {key_low!r} at {path or 'payload'}",
                )
            _scan_payload(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _scan_payload(item, f"{path}[{idx}]")
    elif isinstance(obj, str):
        if _SECRET_VALUE_RE.search(obj):
            raise AgentEventError(
                "secret_detected",
                f"secret-like material rejected at {path or 'payload'}",
            )


class MeasuredUsage(BaseModel):
    """Measured token/latency/cost fields; nullable when unmeasured, never fabricated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: Annotated[float | None, AfterValidator(_finite_nonneg)] = None
    cost_usd: Annotated[float | None, AfterValidator(_finite_nonneg)] = None


class AgentStreamEvent(BaseModel):
    """Strict v1 contract for structured agent/subagent streaming events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent_event.v1"] = AGENT_EVENT_SCHEMA_VERSION
    event_id: CleanId = Field(default_factory=lambda: f"aevt_{uuid4().hex[:16]}")
    event_type: AgentEventType
    run_id: CleanId
    conversation_id: CleanId
    agent_id: CleanId
    parent_agent_id: CleanId | None = None
    sequence: int = Field(ge=1)
    timestamp: str = Field(default_factory=utcnow_isoformat)
    trace_id: CleanId | None = None
    tool_call_id: CleanId | None = None
    artifact_ref: CleanId | None = None
    tenant_id: CleanId = "default"
    usage: MeasuredUsage | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_run_event(self) -> RunEvent:
        """Encode onto the existing `RunEvent` envelope for Redis Streams / SSE."""
        encoded_payload = {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "trace_id": self.trace_id,
            "artifact_ref": self.artifact_ref,
            "tenant_id": self.tenant_id,
            "usage": self.usage.model_dump() if self.usage else None,
            "data": self.payload,
        }
        return RunEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            sequence=self.sequence,
            tool_call_id=self.tool_call_id,
            timestamp=self.timestamp,
            payload=encoded_payload,
        )

    @classmethod
    def from_run_event(cls, event: RunEvent) -> AgentStreamEvent:
        """Decode a `RunEvent` carrying `agent_event.v1` back into a validated event."""
        p = event.payload
        if p.get("schema_version") != AGENT_EVENT_SCHEMA_VERSION:
            raise AgentEventError(
                "unsupported_schema",
                f"expected {AGENT_EVENT_SCHEMA_VERSION}, got {p.get('schema_version')!r}",
            )
        return validate_agent_event(
            {
                "schema_version": p["schema_version"],
                "event_id": event.event_id,
                "event_type": event.event_type,
                "run_id": event.run_id,
                "conversation_id": event.conversation_id,
                "agent_id": p.get("agent_id"),
                "parent_agent_id": p.get("parent_agent_id"),
                "sequence": event.sequence,
                "timestamp": event.timestamp,
                "trace_id": p.get("trace_id"),
                "tool_call_id": event.tool_call_id,
                "artifact_ref": p.get("artifact_ref"),
                "tenant_id": p.get("tenant_id", "default"),
                "usage": p.get("usage"),
                "payload": p.get("data") or {},
            }
        )


def validate_agent_event(raw: dict[str, Any]) -> AgentStreamEvent:
    """Validate schema, privacy rules, and byte-size cap for an agent stream event."""
    try:
        event = AgentStreamEvent.model_validate(raw)
    except ValidationError as exc:
        raise AgentEventError("invalid_schema", str(exc)) from exc
    _scan_payload(event.payload)
    encoded_bytes = len(
        json.dumps(event.payload, separators=(",", ":")).encode("utf-8")
    )
    if encoded_bytes > MAX_EVENT_PAYLOAD_BYTES:
        raise AgentEventError(
            "payload_too_large",
            f"payload is {encoded_bytes} bytes (max {MAX_EVENT_PAYLOAD_BYTES})",
        )
    if event.event_type in ("tool.started", "tool.finished") and not event.tool_call_id:
        raise AgentEventError(
            "missing_correlation_id",
            f"{event.event_type} requires tool_call_id",
        )
    if event.event_type == "artifact.ready" and not event.artifact_ref:
        raise AgentEventError(
            "missing_correlation_id",
            "artifact.ready requires artifact_ref",
        )
    return event


class AgentEventEmitter:
    """Monotonic, tenant-scoped emitter bridging `agent_event.v1` onto `EventPublisher`."""

    def __init__(
        self,
        publisher: EventPublisher,
        *,
        run_id: str,
        conversation_id: str,
        trace_id: str | None = None,
        tenant_id: str = "default",
    ) -> None:
        self._publisher = publisher
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.trace_id = trace_id
        self.tenant_id = tenant_id
        self._seq = 0
        self._cancelled = False
        self.emitted: list[AgentStreamEvent] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def emit(
        self,
        event_type: AgentEventType,
        *,
        agent_id: str,
        parent_agent_id: str | None = None,
        tool_call_id: str | None = None,
        artifact_ref: str | None = None,
        usage: MeasuredUsage | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentStreamEvent | None:
        """Validate and publish one event; suppresses non-cancellation events after cancel."""
        if self._cancelled and event_type != "agent.cancelled":
            return None
        self._seq += 1
        event = validate_agent_event(
            {
                "event_type": event_type,
                "run_id": self.run_id,
                "conversation_id": self.conversation_id,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
                "sequence": self._seq,
                "trace_id": self.trace_id,
                "tool_call_id": tool_call_id,
                "artifact_ref": artifact_ref,
                "tenant_id": self.tenant_id,
                "usage": usage.model_dump() if usage else None,
                "payload": payload or {},
            }
        )
        self.emitted.append(event)
        self._publisher.publish(event.to_run_event())
        return event

    def cancel(
        self,
        *,
        agent_id: str,
        parent_agent_id: str | None = None,
        reason: str = "user_requested",
    ) -> AgentStreamEvent | None:
        """Mark run cancelled and emit `agent.cancelled` once per agent."""
        self._cancelled = True
        return self.emit(
            "agent.cancelled",
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            payload={"reason": reason, "status": "cancelled"},
        )


def replay_deduplicated(
    events: list[AgentStreamEvent],
    *,
    run_id: str,
    tenant_id: str = "default",
    after_sequence: int = 0,
) -> list[AgentStreamEvent]:
    """Return ordered, deduplicated replay events strictly scoped to `(run_id, tenant_id)`."""
    seen_seq: set[int] = set()
    seen_ids: set[str] = set()
    filtered: list[AgentStreamEvent] = []
    for ev in sorted(events, key=lambda e: (e.sequence, e.event_id)):
        if ev.run_id != run_id or ev.tenant_id != tenant_id:
            continue
        if ev.sequence <= after_sequence:
            continue
        if ev.sequence in seen_seq or ev.event_id in seen_ids:
            continue
        seen_seq.add(ev.sequence)
        seen_ids.add(ev.event_id)
        filtered.append(ev)
    return filtered


def build_concurrent_specialist_fixture(
    *,
    publisher: EventPublisher,
    run_id: str = "run_fixture_232",
    conversation_id: str = "conv_fixture_232",
    trace_id: str = "0af7651916cd43dd8448eb211c80319c",
    tenant_id: str = "default",
    simulate_cancel: bool = False,
) -> list[AgentStreamEvent]:
    """Emit a deterministic two-specialist concurrent fixture (with one tool error + retry)."""
    emitter = AgentEventEmitter(
        publisher,
        run_id=run_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
    )
    emitter.emit(
        "agent.started",
        agent_id="coordinator",
        payload={"role": "Coordinator", "status": "running"},
    )
    emitter.emit(
        "agent.started",
        agent_id="spatial-analyst",
        parent_agent_id="coordinator",
        payload={"role": "Spatial Analyst", "status": "running"},
    )
    emitter.emit(
        "agent.started",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        payload={"role": "Trend Analyst", "status": "running"},
    )
    emitter.emit(
        "tool.started",
        agent_id="spatial-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_map_1",
        payload={"tool_name": "map_taxi_zones", "metric": "trip_count"},
    )
    emitter.emit(
        "tool.started",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_trend_1",
        payload={
            "tool_name": "aggregate_taxi_data",
            "dimensions": ["pickup_week_part"],
        },
    )
    if simulate_cancel:
        emitter.cancel(agent_id="spatial-analyst", parent_agent_id="coordinator")
        emitter.cancel(agent_id="trend-analyst", parent_agent_id="coordinator")
        emitter.cancel(agent_id="coordinator")
        return emitter.emitted

    # Trend analyst hits a bounded transient tool failure, then succeeds on retry.
    emitter.emit(
        "tool.finished",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_trend_1",
        payload={
            "tool_name": "aggregate_taxi_data",
            "status": "failed",
            "error_code": "timeout",
        },
    )
    emitter.emit(
        "agent.progress",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        payload={"status": "running", "note": "retrying bounded query (attempt 2/2)"},
    )
    emitter.emit(
        "tool.started",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_trend_2",
        payload={
            "tool_name": "aggregate_taxi_data",
            "dimensions": ["pickup_week_part"],
        },
    )
    emitter.emit(
        "tool.finished",
        agent_id="spatial-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_map_1",
        usage=MeasuredUsage(latency_ms=42.5),
        payload={
            "tool_name": "map_taxi_zones",
            "status": "completed",
            "row_count": 263,
        },
    )
    emitter.emit(
        "artifact.ready",
        agent_id="spatial-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_map_1",
        artifact_ref="art_map_pickup_v1",
        payload={"artifact_kind": "map", "title": "Pickup count by taxi zone"},
    )
    emitter.emit(
        "agent.completed",
        agent_id="spatial-analyst",
        parent_agent_id="coordinator",
        usage=MeasuredUsage(
            input_tokens=410, output_tokens=95, latency_ms=180.0, cost_usd=0.0002
        ),
        payload={"status": "completed", "artifact_refs": ["art_map_pickup_v1"]},
    )
    emitter.emit(
        "tool.finished",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_trend_2",
        usage=MeasuredUsage(latency_ms=31.0),
        payload={
            "tool_name": "aggregate_taxi_data",
            "status": "completed",
            "row_count": 2,
        },
    )
    emitter.emit(
        "artifact.ready",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        tool_call_id="tc_trend_2",
        artifact_ref="art_chart_weekpart_v1",
        payload={
            "artifact_kind": "segment_comparison",
            "title": "Weekday vs weekend trips",
        },
    )
    emitter.emit(
        "agent.completed",
        agent_id="trend-analyst",
        parent_agent_id="coordinator",
        usage=MeasuredUsage(
            input_tokens=380, output_tokens=88, latency_ms=210.0, cost_usd=0.00018
        ),
        payload={"status": "completed", "artifact_refs": ["art_chart_weekpart_v1"]},
    )
    emitter.emit(
        "agent.completed",
        agent_id="coordinator",
        usage=MeasuredUsage(
            input_tokens=620, output_tokens=140, latency_ms=430.0, cost_usd=0.00031
        ),
        payload={
            "status": "completed",
            "artifact_refs": ["art_map_pickup_v1", "art_chart_weekpart_v1"],
        },
    )
    return emitter.emitted

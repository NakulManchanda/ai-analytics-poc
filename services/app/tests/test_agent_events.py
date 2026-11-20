"""Contract and unit tests for #232 versioned structured agent/subagent streaming events."""

from __future__ import annotations

import pytest
from app.events.agent_events import (
    AGENT_EVENT_SCHEMA_VERSION,
    MAX_EVENT_PAYLOAD_BYTES,
    AgentEventEmitter,
    AgentEventError,
    AgentStreamEvent,
    MeasuredUsage,
    build_concurrent_specialist_fixture,
    replay_deduplicated,
    validate_agent_event,
)
from app.events.models import RunEvent
from app.events.publisher import InMemoryEventPublisher


def test_concurrent_specialist_fixture_roundtrips_through_run_event_and_sse() -> None:
    pub = InMemoryEventPublisher()
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    events = build_concurrent_specialist_fixture(publisher=pub, trace_id=trace_id)

    assert len(events) == len(pub.events)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert all(e.schema_version == AGENT_EVENT_SCHEMA_VERSION for e in events)
    assert all(e.trace_id == trace_id for e in events)

    # Parallel branches share coordinator as parent_agent_id.
    children = {e.agent_id for e in events if e.parent_agent_id == "coordinator"}
    assert children == {"spatial-analyst", "trend-analyst"}

    # Roundtrip through Redis/dict serialization and RunEvent SSE framing.
    for orig, run_ev in zip(events, pub.events, strict=True):
        rehydrated = RunEvent.from_dict(run_ev.to_dict())
        decoded = AgentStreamEvent.from_run_event(rehydrated)
        assert decoded == orig
        sse_frame = run_ev.to_sse()
        assert f"event: {orig.event_type}\n" in sse_frame
        assert trace_id in sse_frame


def test_reconnect_replay_deduplicates_and_enforces_tenant_isolation() -> None:
    pub = InMemoryEventPublisher()
    events = build_concurrent_specialist_fixture(publisher=pub, tenant_id="tenant-a")
    other_pub = InMemoryEventPublisher()
    other_events = build_concurrent_specialist_fixture(
        publisher=other_pub, run_id="run_fixture_232", tenant_id="tenant-b"
    )

    # Mix duplicates, out-of-order frames, and another tenant's events for the same run_id.
    mixed = list(reversed(events)) + events[:4] + other_events
    replayed = replay_deduplicated(
        mixed, run_id="run_fixture_232", tenant_id="tenant-a", after_sequence=5
    )

    assert [e.sequence for e in replayed] == list(range(6, len(events) + 1))
    assert all(e.tenant_id == "tenant-a" for e in replayed)


def test_cancellation_suppresses_subsequent_non_cancel_events() -> None:
    pub = InMemoryEventPublisher()
    events = build_concurrent_specialist_fixture(publisher=pub, simulate_cancel=True)
    types = [e.event_type for e in events]
    assert types.count("agent.cancelled") == 3
    assert "agent.completed" not in types

    emitter = AgentEventEmitter(pub, run_id="r_c", conversation_id="c_c")
    emitter.emit("agent.started", agent_id="coord")
    emitter.cancel(agent_id="coord")
    suppressed = emitter.emit("agent.completed", agent_id="coord")
    assert suppressed is None


@pytest.mark.parametrize(
    ("bad_payload", "expected_code"),
    [
        ({"chain_of_thought": "secret reasoning"}, "forbidden_field"),
        ({"nested": {"thinking": "step 1"}}, "forbidden_field"),
        ({"progress_percent": 73}, "forbidden_field"),
        ({"api_key": "abc"}, "forbidden_field"),
        ({"note": "Bearer sk-1234567890abcdef1234567890"}, "secret_detected"),
        ({"blob": "x" * (MAX_EVENT_PAYLOAD_BYTES + 10)}, "payload_too_large"),
    ],
)
def test_privacy_size_and_fabricated_progress_guards(
    bad_payload: dict, expected_code: str
) -> None:
    with pytest.raises(AgentEventError) as info:
        validate_agent_event(
            {
                "event_type": "agent.progress",
                "run_id": "r1",
                "conversation_id": "c1",
                "agent_id": "a1",
                "sequence": 1,
                "payload": bad_payload,
            }
        )
    assert info.value.code == expected_code


def test_correlation_ids_required_for_tool_and_artifact_events() -> None:
    with pytest.raises(AgentEventError) as info:
        validate_agent_event(
            {
                "event_type": "tool.started",
                "run_id": "r1",
                "conversation_id": "c1",
                "agent_id": "a1",
                "sequence": 1,
            }
        )
    assert info.value.code == "missing_correlation_id"

    with pytest.raises(AgentEventError) as info2:
        validate_agent_event(
            {
                "event_type": "artifact.ready",
                "run_id": "r1",
                "conversation_id": "c1",
                "agent_id": "a1",
                "sequence": 2,
            }
        )
    assert info2.value.code == "missing_correlation_id"

    # Nullable usage fields remain None rather than zero.
    ev = validate_agent_event(
        {
            "event_type": "agent.completed",
            "run_id": "r1",
            "conversation_id": "c1",
            "agent_id": "a1",
            "sequence": 3,
            "usage": MeasuredUsage(latency_ms=12.5).model_dump(),
        }
    )
    assert ev.usage is not None
    assert ev.usage.input_tokens is None and ev.usage.cost_usd is None

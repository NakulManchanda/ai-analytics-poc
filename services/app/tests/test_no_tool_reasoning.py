"""#186 slice C: blank content + hidden reasoning is a distinct no_visible_output outcome.
The reasoning text is never read, stored, logged, emitted or captured."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from app.config import VoiceSettings
from app.events import InMemoryEventPublisher
from app.llm import LiteLLMClient, LLMProviderError
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository
from test_orchestration_loop import FakeMCPClient

SECRET = "SECRET-REASONING-48151623"
QUESTION = "Does weather affect tipping in taxi trips?"
SCHEMA = {"dataset": "nyc-taxi", "month": "2024-01", "columns": ["PULocationID"]}


def _body(
    message: dict[str, Any],
    details: dict[str, Any] | None = None,
    finish_reason: str = "stop",
):
    usage: dict[str, Any] = {"prompt_tokens": 100, "completion_tokens": 88}
    if details is not None:
        usage["completion_tokens_details"] = details
    return {
        "model": "taxi-planner",
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


def _reasoning_message(content: str = " ") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "reasoning_content": f"The tools cannot answer this. {SECRET}",
        "thinking_blocks": [{"type": "thinking", "thinking": SECRET}],
    }


def _client(body: dict[str, Any]) -> LiteLLMClient:
    return LiteLLMClient(
        "http://litellm:4000",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    )


def _propose(body: dict[str, Any]):
    return _client(body).propose_taxi_query(QUESTION, SCHEMA)


def test_blank_content_with_reasoning_is_no_visible_output() -> None:
    result = _propose(_body(_reasoning_message(), {"reasoning_tokens": 74}))
    assert result.outcome == "no_visible_output"
    assert not result.decline_reason
    assert result.output_basis == "reasoning_only"
    assert result.finish_reason == "stop"
    assert result.reasoning_present is True
    assert result.reasoning_tokens == 74
    assert result.reasoning_chars and result.reasoning_chars > 0
    assert SECRET not in repr(result)


def test_length_finish_reason_is_reasoning_length() -> None:
    body = _body(_reasoning_message(), {"reasoning_tokens": 74}, "length")
    result = _propose(body)
    assert result.outcome == "no_visible_output"
    assert result.output_basis == "reasoning_length"
    assert result.finish_reason == "length"
    assert SECRET not in repr(result)


def test_reasoning_tokens_alone_count_as_reasoning() -> None:
    body = _body({"role": "assistant", "content": " "}, {"reasoning_tokens": 5})
    result = _propose(body)
    assert result.outcome == "no_visible_output"
    assert result.output_basis == "reasoning_only"
    assert result.reasoning_chars == 0


def test_blank_content_without_reasoning_still_fails() -> None:
    body = _body({"role": "assistant", "content": " "}, {"reasoning_tokens": 0})
    with pytest.raises(LLMProviderError) as exc:
        _propose(body)
    assert exc.value.code == "no_tool_call"


def test_content_based_classification_unchanged() -> None:
    msg = {
        "role": "assistant",
        "content": "That is outside the dataset and not supported.",
        "reasoning_content": SECRET,
    }
    result = _propose(_body(msg))
    assert result.outcome != "tool_call"
    assert result.output_basis == "content"
    assert result.reasoning_present is None
    assert result.decline_reason == msg["content"]


def test_tool_call_response_unchanged() -> None:
    msg = {
        "role": "assistant",
        "content": " ",
        "reasoning_content": SECRET,
        "tool_calls": [
            {
                "id": "t1",
                "type": "function",
                "function": {"name": "aggregate_taxi_data", "arguments": "{}"},
            }
        ],
    }
    result = _propose(_body(msg))
    assert result.outcome == "tool_call" and result.name == "aggregate_taxi_data"
    assert result.output_basis is None and result.reasoning_present is None


def test_loop_handles_no_visible_output_and_leaks_nothing(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    publisher = InMemoryEventPublisher()
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=_client(_body(_reasoning_message(), {"reasoning_tokens": 74})),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
        voice_settings=VoiceSettings(enabled=False),
    )
    result = loop.run(QUESTION)
    assert "did not return a usable response" in result.answer
    lowered = result.answer.lower()
    assert "decline" not in lowered and "cannot" not in lowered
    assert "dataset does not" not in lowered
    run_id = next(iter(repo._runs))
    events = publisher.get_events_for_run(run_id)
    declined = [e for e in events if e.event_type == "run.declined"]
    assert len(declined) == 1
    assert declined[0].payload["outcome"] == "no_visible_output"
    assert not declined[0].payload["reason"]
    assert not [e for e in events if e.event_type == "run.failed"]
    run = repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    assert run.metadata["telemetry"]["planner_outcome"] == "no_visible_output"
    steps = repo.list_run_steps(run_id)
    (proposal_step,) = [st for st in steps if st.step_type == "llm_proposal"]
    assert proposal_step.metadata["outcome"] == "no_visible_output"
    assert proposal_step.metadata["output_basis"] == "reasoning_only"
    call = run.metadata["llm_calls"][0]
    assert call["outcome"] == "no_visible_output"
    assert call["output_basis"] == "reasoning_only"
    assert call["reasoning_present"] is True and call["reasoning_tokens"] == 74
    blob = (
        json.dumps([e.payload for e in events], default=str)
        + json.dumps(run.metadata, default=str)
        + result.answer
        + caplog.text
        + repr(repo._messages)
        + repr(steps)
    )
    assert SECRET not in blob


def test_capture_record_has_safe_facts_and_no_reasoning_text(tmp_path) -> None:
    from app.capture.recorder import CaptureRecorder, CaptureSettings

    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1")
    loop = OrchestrationLoop(
        llm_client=_client(_body(_reasoning_message(), {"reasoning_tokens": 74})),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=recorder,
    )
    loop.run(QUESTION)
    text = (tmp_path / "t1" / "captures.jsonl").read_text()
    assert SECRET not in text
    recs = [json.loads(line) for line in text.splitlines()]
    (call,) = [r for r in recs if r["record_type"] == "model_call"]
    assert call["output_basis"] == "reasoning_only"
    assert call["outcome"] == "no_visible_output"
    assert call["reasoning_present"] is True
    assert call["reasoning_tokens"] == 74 and call["reasoning_chars"] > 0

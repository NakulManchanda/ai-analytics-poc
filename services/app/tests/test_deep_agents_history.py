from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.orchestration import deep_agents_run
from app.orchestration.deep_agents_run import (
    HISTORY_MAX_TURNS,
    build_history_messages,
)
from app.state import Conversation, Message
from app.state.repository import InMemoryStateRepository
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from test_deep_agents_run import (
    ARGS,
    DEFINITIONS,
    FakeMCP,
    _ai,
    _setup,
    _tool,
)


def _repo(*specs: tuple[str, str, str, dict[str, Any] | None], conv: str = "c1"):
    repo = InMemoryStateRepository()
    repo.create_conversation(Conversation(conversation_id=conv))
    for i, (role, content, mid, meta) in enumerate(specs, start=1):
        repo.add_message(
            Message(
                message_id=mid,
                conversation_id=conv,
                sequence=i,
                role=role,
                content=content,
                metadata=meta or {},
            )
        )
    return repo


def _roles(history: list[dict[str, str]]) -> list[str]:
    return [m["role"] for m in history]


def _assert_alternating(history: list[dict[str, str]]) -> None:
    for i, m in enumerate(history):
        assert m["role"] == ("user" if i % 2 == 0 else "assistant")


def test_folds_tool_result_and_arguments_into_following_assistant() -> None:
    repo = _repo(
        ("user", "top zones", "m1", None),
        (
            "tool",
            '{"rows":[["JFK",5]]}',
            "m2",
            {"tool_name": "aggregate_taxi_data", "arguments": ARGS},
        ),
        ("assistant", "JFK leads.", "m3", None),
        ("user", "now", "m4", None),
    )
    history = build_history_messages(repo, "c1", "m4")
    assert _roles(history) == ["user", "assistant"]
    assert history[0]["content"] == "top zones"
    text = history[1]["content"]
    assert "aggregate_taxi_data" in text
    assert json.dumps(ARGS, sort_keys=True) in text
    assert '{"rows":[["JFK",5]]}' in text
    assert text.endswith("JFK leads.")


def test_legacy_tool_message_without_arguments_omits_them() -> None:
    repo = _repo(
        ("user", "q", "m1", None),
        ("tool", "{}", "m2", {"tool_name": "describe_taxi_dataset"}),
        ("assistant", "a", "m3", None),
        ("user", "now", "m4", None),
    )
    text = build_history_messages(repo, "c1", "m4")[1]["content"]
    assert "describe_taxi_dataset" in text and "arguments" not in text


def test_conversation_isolation() -> None:
    repo = _repo(
        ("user", "mine", "m1", None),
        ("assistant", "mine too", "m2", None),
        ("user", "now", "m3", None),
    )
    repo.create_conversation(Conversation(conversation_id="c2"))
    repo.add_message(Message("x1", "c2", 1, "user", "other secret question"))
    repo.add_message(Message("x2", "c2", 2, "assistant", "other secret answer"))
    blob = json.dumps(build_history_messages(repo, "c1", "m3"))
    assert "mine" in blob and "other secret" not in blob


def test_current_turn_user_message_dropped() -> None:
    repo = _repo(("user", "current", "m1", None))
    assert build_history_messages(repo, "c1", "m1") == []


def test_truncation_marker_and_bound() -> None:
    repo = _repo(
        ("user", "q", "m1", None),
        ("tool", "x" * 500, "m2", {"tool_name": "t"}),
        ("assistant", "a", "m3", None),
        ("user", "now", "m4", None),
    )
    text = build_history_messages(repo, "c1", "m4", max_tool_chars=100)[1]["content"]
    assert "[truncated]" in text
    assert "x" * 100 in text and "x" * 101 not in text


def test_no_truncation_marker_when_within_bound() -> None:
    repo = _repo(
        ("user", "q", "m1", None),
        ("tool", "short", "m2", {"tool_name": "t"}),
        ("assistant", "a", "m3", None),
        ("user", "now", "m4", None),
    )
    assert "[truncated]" not in build_history_messages(repo, "c1", "m4")[1]["content"]


def test_only_last_n_turns_kept() -> None:
    specs: list[tuple[str, str, str, dict[str, Any] | None]] = []
    for i in range(6):
        specs.append(("user", f"q{i}", f"u{i}", None))
        specs.append(("assistant", f"a{i}", f"a{i}", None))
    specs.append(("user", "now", "cur", None))
    history = build_history_messages(_repo(*specs), "c1", "cur", max_turns=2)
    assert [m["content"] for m in history] == ["q4", "a4", "q5", "a5"]
    default = build_history_messages(_repo(*specs), "c1", "cur")
    assert len(default) == 2 * HISTORY_MAX_TURNS
    assert build_history_messages(_repo(*specs), "c1", "cur", max_turns=0) == []


def test_tool_without_following_assistant_gets_synthetic_assistant() -> None:
    repo = _repo(
        ("user", "q1", "m1", None),
        (
            "tool",
            '{"r":1}',
            "m2",
            {"tool_name": "aggregate_taxi_data", "arguments": ARGS},
        ),
        # run failed: no assistant message
        ("user", "q2", "m3", None),
        ("assistant", "a2", "m4", None),
        ("user", "now", "m5", None),
    )
    history = build_history_messages(repo, "c1", "m5")
    _assert_alternating(history)
    assert _roles(history) == ["user", "assistant", "user", "assistant"]
    assert '{"r":1}' in history[1]["content"]
    assert "No answer was recorded" in history[1]["content"]


def test_trailing_tool_without_assistant() -> None:
    repo = _repo(
        ("user", "q1", "m1", None),
        ("tool", '{"r":1}', "m2", {"tool_name": "t"}),
        ("user", "now", "m3", None),
    )
    history = build_history_messages(repo, "c1", "m3")
    _assert_alternating(history)
    assert _roles(history) == ["user", "assistant"]


def test_consecutive_user_messages_are_merged_and_trailing_user_closed() -> None:
    repo = _repo(
        ("user", "first", "m1", None),
        ("user", "second", "m2", None),
        ("assistant", "ok", "m3", None),
        ("user", "third", "m4", None),  # failed turn, no assistant
        ("user", "now", "m5", None),
    )
    history = build_history_messages(repo, "c1", "m5")
    _assert_alternating(history)
    assert history[0]["content"] == "first\n\nsecond"
    assert history[-1]["role"] == "assistant"
    assert all(m["role"] in ("user", "assistant") for m in history)


def test_empty_conversation() -> None:
    assert build_history_messages(_repo(), "c1", "none") == []


# -- end to end ----------------------------------------------------------------------
class RecordingModel(BaseChatModel):
    script: list[Any]
    seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def _two_turn_setup(monkeypatch: pytest.MonkeyPatch, script: list[Any], mcp: Any):
    model = RecordingModel(script=script, seen=[])
    real = deep_agents_run.build_deep_agent

    def fake_build(**kwargs: Any) -> Any:
        return real(
            **{**kwargs, "chat_model": model, "list_tools": lambda: DEFINITIONS}
        )

    monkeypatch.setattr(deep_agents_run, "build_deep_agent", fake_build)
    loop, repo, _pub = _setup(monkeypatch, [], mcp=mcp)
    monkeypatch.setattr(deep_agents_run, "build_deep_agent", fake_build)
    return loop, repo, model


def _flatten(messages: list[Any]) -> str:
    return "\n".join(str(getattr(m, "content", m)) for m in messages)


def test_two_turn_run_restores_context_and_excludes_other_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_payload = {
        "query_id": "qid_1",
        "row_count": 2,
        "basis": "trips",
        "rows": [
            {"pickup_zone": "JFK", "trip_count": 900},
            {"pickup_zone": "LGA", "trip_count": 700},
        ],
    }
    loop, repo, model = _two_turn_setup(
        monkeypatch,
        [
            _tool("aggregate_taxi_data", ARGS),
            _ai("JFK is the top pickup zone."),
            _ai("LGA is 200 trips behind JFK."),
        ],
        FakeMCP(result=result_payload),
    )
    other = loop.prepare_run("OTHER-CONVERSATION-QUESTION")
    repo.add_message(
        Message(
            "other_a",
            other.conversation_id,
            2,
            "assistant",
            "OTHER-CONVERSATION-ANSWER",
        )
    )

    t1 = loop.prepare_run("top pickup zones")
    assert loop.execute(t1).status == "completed"
    t2 = loop.prepare_run(
        "Compare that with the second highest zone", conversation_id=t1.conversation_id
    )
    result = loop.execute(t2)
    assert result.status == "completed"

    # the stored tool message carries the arguments
    tool_msgs = [m for m in repo.list_messages(t1.conversation_id) if m.role == "tool"]
    assert tool_msgs[0].metadata["arguments"] == ARGS

    turn2_input = model.seen[-1]
    text = _flatten(turn2_input)
    assert "top pickup zones" in text
    assert "JFK is the top pickup zone." in text
    assert "aggregate_taxi_data" in text
    assert "pickup_zone" in text and "trip_count" in text  # arguments + result
    assert '"basis":"trips"' in text and "LGA" in text
    assert "Compare that with the second highest zone" in text
    assert "OTHER-CONVERSATION" not in text
    assert not any(getattr(m, "type", None) == "tool" for m in turn2_input)
    assert turn2_input[-1].type == "human"
    # turn 1 saw no history
    assert "JFK is the top" not in _flatten(model.seen[0])


def test_correlation_failure_visible_in_metadata_and_run_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)  # no billed-cost header

    from test_deep_agents_run import _setup_http

    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("inject failed")

    monkeypatch.setattr(deep_agents_run.propagate, "inject", boom)
    loop, _pub, _created, _closes, _models = _setup_http(
        monkeypatch, [_ai("done")], handler
    )
    submission = loop.prepare_run("q")
    result = loop.execute(submission)
    assert result.status == "completed"
    run = loop._repo.get_run(submission.run_id)
    assert run.metadata["correlation_ok"] is False
    assert run.metadata["correlation"]["header_failures"] >= 1
    assert run.metadata["correlation"]["cost_header_missing"] >= 1


def test_correlation_clean_run_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, repo, _pub = _setup(monkeypatch, [_ai("done")])
    submission = loop.prepare_run("q")
    assert loop.execute(submission).status == "completed"
    run = repo.get_run(submission.run_id)
    assert run.metadata["correlation_ok"] is True
    assert run.metadata["correlation"] == {
        "header_failures": 0,
        "cost_header_missing": 0,
    }


def test_system_prompt_asks_for_clarification() -> None:
    from app.orchestration.deep_agents_strategy import build_system_prompt

    assert "clarifying question" in build_system_prompt()

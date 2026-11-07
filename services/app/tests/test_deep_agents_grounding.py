from __future__ import annotations

from typing import Any

import pytest
from app.mcp_client import MCPToolError
from tests.test_deep_agents_run import (
    ARGS,
    FakeMCP,
    _ai,
    _setup,
    _tool,
)

RESULT = {
    "query_id": "qid_1",
    "row_count": 2,
    "rows": [["JFK", 15000], ["LGA", 12000]],
}


class SeqMCP(FakeMCP):
    def __init__(self, results: list[Any]):
        super().__init__()
        self.results = results

    def aggregate_taxi_data(self, **kw: Any) -> Any:
        self.calls += 1
        return self.results.pop(0)


def _run(monkeypatch, script, mcp=None, prompt="q"):
    loop, repo, pub = _setup(monkeypatch, script, mcp=mcp or FakeMCP(RESULT))
    sub = loop.prepare_run(prompt)
    result = loop.execute(sub)
    run = repo.get_run(sub.run_id)
    answers = [
        e.payload["answer"] for e in pub.events if e.event_type == "answer.completed"
    ]
    persisted = repo.list_messages(sub.conversation_id)[-1].content
    return result, run, answers, persisted


def _grounding_steps(result):
    return [s for s in result.steps if s.step_type == "grounding_check"]


def _assert_consistent(result, answers, persisted):
    assert answers == [result.answer] and persisted == result.answer


def test_supported_number_has_no_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    result, run, answers, persisted = _run(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("JFK had 15,000 trips.")],
    )
    assert result.answer == "JFK had 15,000 trips."
    assert not _grounding_steps(result)
    _assert_consistent(result, answers, persisted)
    assert run.metadata["grounding_report"]["ok"] is True


def test_unsupported_number_gets_one_caveat_and_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, run, answers, persisted = _run(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("JFK had 98,765 trips.")],
    )
    assert result.answer.count("Note:") == 1
    assert result.answer.startswith("JFK had 98,765 trips.\n\nNote:")
    _assert_consistent(result, answers, persisted)
    (step,) = _grounding_steps(result)
    assert step.status == "failed" and step.query_id == "qid_1"
    assert step.metadata["ungrounded_numbers"] == [98765.0]
    assert [s.sequence for s in result.steps] == list(range(1, len(result.steps) + 1))
    assert run.metadata["grounding_report"] == {
        "ok": False,
        "ungrounded_numbers": 1,
        "unknown_query_ids": 0,
        "evidence_count": 1,
    }


def test_number_supported_only_by_first_of_two_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second = {"query_id": "qid_2", "row_count": 1, "rows": [["z", 5]]}
    mcp = SeqMCP([RESULT, second])
    other = {"dimensions": ["pickup_zone"], "measures": ["trip_count"], "limit": 3}
    result, run, answers, persisted = _run(
        monkeypatch,
        [
            _tool("aggregate_taxi_data", ARGS, "a"),
            _tool("aggregate_taxi_data", other, "b"),
            _ai("JFK had 15,000 trips (query id qid_1)."),
        ],
        mcp=mcp,
    )
    assert mcp.calls == 2
    assert result.answer == "JFK had 15,000 trips (query id qid_1)."
    assert not _grounding_steps(result)
    assert run.metadata["grounding_report"]["evidence_count"] == 2


def test_cited_unknown_query_id(monkeypatch: pytest.MonkeyPatch) -> None:
    result, run, answers, persisted = _run(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("See query id bogus-9 for details.")],
    )
    (step,) = _grounding_steps(result)
    assert step.metadata["unknown_query_ids"] == ["bogus-9"]
    assert "do not match" in result.answer
    _assert_consistent(result, answers, persisted)


def test_no_evidence_numeric_claim_gets_caveat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, run, answers, persisted = _run(
        monkeypatch, [_ai("There were 98,765 trips.")]
    )
    assert result.answer.count("Note:") == 1
    assert len(_grounding_steps(result)) == 1
    assert _grounding_steps(result)[0].query_id is None
    _assert_consistent(result, answers, persisted)
    assert run.metadata["grounding_report"]["evidence_count"] == 0


def test_no_evidence_no_numbers_no_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    result, run, answers, persisted = _run(
        monkeypatch, [_ai("Which month do you mean?")]
    )
    assert result.answer == "Which month do you mean?"
    assert not _grounding_steps(result)
    _assert_consistent(result, answers, persisted)


def test_failed_tool_call_is_not_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = FakeMCP(error=MCPToolError(False, "sql failed", reason="invalid_request"))
    result, run, answers, persisted = _run(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("It returned 15,000 trips.")],
        mcp=mcp,
    )
    assert len(_grounding_steps(result)) == 1
    assert run.metadata["grounding_report"]["evidence_count"] == 0
    _assert_consistent(result, answers, persisted)

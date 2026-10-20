"""Integration: invalid proposals never reach a governed tool; bounded retry; grounding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from app.config import DEFAULT_MODEL_ID
from app.llm import LLMResult, LocalFakeLLMClient, ToolProposalResult
from app.mcp_client import MCPToolError
from app.orchestration import ExecutionBudgets, OrchestrationLoop
from app.state import InMemoryStateRepository

QUESTION = "What are the top pickup zones by ride count?"
GOOD = ("query_taxi_data", {"analysis": "top_pickup_zones", "limit": 5})


class RecordingMCP:
    def __init__(self, rows: list[list[object]] | None = None, fail: bool = False):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows = rows or [["JFK Airport", 1500], ["LaGuardia Airport", 1200]]
        self.fail = fail

    def get_dataset_schema(self) -> dict[str, Any]:
        return {"dataset": "nyc-taxi", "month": "2024-01", "columns": ["fare_amount"]}

    def query_taxi_data(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("run_id", None)
        self.calls.append(("query_taxi_data", kwargs))
        if self.fail:
            raise MCPToolError(retryable=False, message="tenant denied")
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": self.rows,
            "row_count": len(self.rows),
            "execution_duration_ms": 12,
            "query_id": "fake-query-123",
            "truncated": False,
        }

    def aggregate_taxi_data(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("run_id", None)
        self.calls.append(("aggregate_taxi_data", kwargs))
        raise AssertionError("must not execute")


class ScriptedLLM(LocalFakeLLMClient):
    def __init__(self, proposals: list[tuple[str, object]], answer: str | None = None):
        self.proposals = proposals
        self.prompts: list[str] = []
        self.answer = answer

    def propose_taxi_query(
        self, prompt: str, schema: Mapping[str, object], **_: Any
    ) -> ToolProposalResult:
        self.prompts.append(prompt)
        name, arguments = self.proposals[
            min(len(self.prompts), len(self.proposals)) - 1
        ]
        return ToolProposalResult(
            name=name,
            arguments=arguments,
            model_id=DEFAULT_MODEL_ID,
            input_tokens=10,
            output_tokens=5,
            latency_ms=0,
        )

    def answer_with_query_result(
        self, prompt: str, query_result: Any, **kw: Any
    ) -> LLMResult:
        if self.answer is None:
            return super().answer_with_query_result(prompt, query_result)
        return LLMResult(
            text=self.answer,
            model_id=DEFAULT_MODEL_ID,
            input_tokens=5,
            output_tokens=5,
            latency_ms=0,
        )


def run_loop(llm, mcp, budgets=None):  # type: ignore[no-untyped-def]
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=llm,
        mcp_client=mcp,
        state_repository=repo,
        budgets=budgets or ExecutionBudgets(),
    )
    return loop, repo


@pytest.mark.parametrize(
    "bad",
    [
        ("non_existent_tool", {"x": 1}),  # malformed / not allowlisted
        ("query_taxi_data", {"analysis": "top_pickup_zones", "limit": 9999}),
        ("query_taxi_data", "not-an-object"),
        (  # unauthorized column/dimension
            "aggregate_taxi_data",
            {"dimensions": ["rider_ssn"], "measures": ["trip_count"], "limit": 5},
        ),
        (  # time scope outside the pinned month: unsupported
            "aggregate_taxi_data",
            {
                "dimensions": ["pickup_date"],
                "measures": ["trip_count"],
                "filters": {"start_timestamp": "2019-01-01T00:00:00"},
                "limit": 5,
            },
        ),
    ],
)
def test_invalid_proposals_never_execute_a_governed_tool(bad) -> None:  # type: ignore[no-untyped-def]
    mcp = RecordingMCP()
    llm = ScriptedLLM([bad])
    loop, repo = run_loop(llm, mcp)
    with pytest.raises(ValueError, match="Invalid tool proposal"):
        loop.run(QUESTION)
    assert mcp.calls == []
    assert len(llm.prompts) <= 2  # at most one bounded retry


def test_recoverable_error_retries_once_with_feedback_then_executes_once() -> None:
    mcp = RecordingMCP()
    llm = ScriptedLLM(
        [("query_taxi_data", {"analysis": "top_pickup_zones", "limit": 500}), GOOD]
    )
    loop, repo = run_loop(llm, mcp)
    result = loop.run(QUESTION)
    assert result.status == "completed"
    assert len(llm.prompts) == 2
    assert QUESTION in llm.prompts[1] and "limit" in llm.prompts[1]
    assert "limit" not in llm.prompts[0]
    assert mcp.calls == [
        ("query_taxi_data", {"analysis": "top_pickup_zones", "limit": 5})
    ]
    # cost/latency accounting: retry call is counted (2 proposals + answer)
    assert len(result.llm_calls) == 3
    steps = [s.step_type for s in repo.list_run_steps(result.run_id)]
    assert steps.count("validation_error") == 1
    assert steps.index("validation_error") < steps.index("tool_call")


def test_retry_is_bounded_to_one() -> None:
    mcp = RecordingMCP()
    llm = ScriptedLLM([("query_taxi_data", {"analysis": "x", "limit": 5})] * 5)
    loop, _ = run_loop(llm, mcp)
    with pytest.raises(ValueError, match="Invalid tool proposal"):
        loop.run(QUESTION)
    assert len(llm.prompts) == 2
    assert mcp.calls == []


def test_unsupported_request_is_not_retried() -> None:
    bad = (
        "aggregate_taxi_data",
        {
            "dimensions": ["pickup_date"],
            "measures": ["trip_count"],
            "filters": {"end_timestamp": "2020-06-01T00:00:00"},
            "limit": 5,
        },
    )
    llm = ScriptedLLM([bad, GOOD])
    loop, _ = run_loop(llm, RecordingMCP())
    with pytest.raises(ValueError):
        loop.run(QUESTION)
    assert len(llm.prompts) == 1


def test_no_retry_when_llm_call_budget_cannot_cover_retry_plus_answer() -> None:
    llm = ScriptedLLM([("query_taxi_data", {"analysis": "x", "limit": 5}), GOOD])
    loop, _ = run_loop(llm, RecordingMCP(), ExecutionBudgets(max_llm_calls=2))
    with pytest.raises(ValueError, match="Invalid tool proposal"):
        loop.run(QUESTION)
    assert len(llm.prompts) == 1


def test_no_retry_when_token_budget_cannot_cover_retry() -> None:
    llm = ScriptedLLM([("query_taxi_data", {"analysis": "x", "limit": 5}), GOOD])
    loop, _ = run_loop(llm, RecordingMCP(), ExecutionBudgets(max_input_tokens=15))
    with pytest.raises(ValueError, match="Invalid tool proposal"):
        loop.run(QUESTION)
    assert len(llm.prompts) == 1


def test_tenant_or_auth_denial_from_mcp_is_not_retried() -> None:
    mcp = RecordingMCP(fail=True)
    llm = ScriptedLLM([GOOD])
    loop, _ = run_loop(llm, mcp)
    with pytest.raises(ValueError):
        loop.run(QUESTION)
    assert len(llm.prompts) == 1
    assert len(mcp.calls) == 1  # executed once, denied, never replayed


def test_fabricated_figures_in_answer_get_deterministic_caveat() -> None:
    llm = ScriptedLLM(
        [GOOD], answer="JFK Airport had 98,765 trips, up 40% on last year."
    )
    loop, repo = run_loop(llm, RecordingMCP())
    result = loop.run(QUESTION)
    assert "could not be verified" in result.answer
    steps = repo.list_run_steps(result.run_id)
    grounding = [s for s in steps if s.step_type == "grounding_check"]
    assert len(grounding) == 1 and grounding[0].status == "failed"
    assert 98765.0 in grounding[0].metadata["ungrounded_numbers"]
    assistant = [
        m for m in repo.list_messages(result.conversation_id) if m.role == "assistant"
    ]
    assert assistant[0].content == result.answer


def test_grounded_answer_has_no_caveat_or_extra_step() -> None:
    llm = ScriptedLLM([GOOD], answer="JFK Airport leads with 1,500 trips.")
    loop, repo = run_loop(llm, RecordingMCP())
    result = loop.run(QUESTION)
    assert "could not be verified" not in result.answer
    assert all(
        s.step_type != "grounding_check" for s in repo.list_run_steps(result.run_id)
    )


def test_raw_arguments_not_persisted_in_steps_or_errors() -> None:
    secret = "SECRET-VALUE-123"
    bad = ("query_taxi_data", {"analysis": secret, "limit": 5})
    llm = ScriptedLLM([bad])
    loop, repo = run_loop(llm, RecordingMCP())
    with pytest.raises(ValueError) as err:
        loop.run(QUESTION)
    assert secret not in str(err.value)
    dumped = repr(repo._run_steps) + repr(repo._runs)
    assert secret not in dumped
    assert "arguments_sha256" in dumped and "argument_keys" in dumped

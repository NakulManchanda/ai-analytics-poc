from __future__ import annotations

import time
from typing import Any

import httpx
import openai
import pytest
from app.events.publisher import InMemoryEventPublisher
from app.llm import LiteLLMClient, LocalFakeLLMClient
from app.mcp_client import MCPToolError
from app.orchestration import deep_agents_run
from app.orchestration.budgets import ExecutionBudgets
from app.orchestration.deep_agents_run import _Correlation
from app.orchestration.deep_agents_strategy import clear_tool_definition_cache
from app.orchestration.loop import OrchestrationError, OrchestrationLoop
from app.state.repository import InMemoryStateRepository
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ARGS = {"dimensions": ["pickup_zone"], "measures": ["trip_count"]}
PROXY = "http://litellm.test:4000"
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_OBJ = {"type": "object", "additionalProperties": True}


def _definition(name: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} description",
        "inputSchema": {"type": "object", "properties": props},
    }


DEFINITIONS = [
    _definition("describe_taxi_dataset", {"include_column_stats": {"type": "boolean"}}),
    _definition(
        "list_taxi_dimension_values",
        {"dimension": {"type": "string"}, "limit": {"type": "integer"}},
    ),
    _definition(
        "aggregate_taxi_data",
        {
            "dimensions": _STR_LIST,
            "measures": _STR_LIST,
            "filters": _OBJ,
            "order_by": _OBJ,
            "limit": {"type": "integer"},
        },
    ),
    _definition(
        "compare_taxi_segments",
        {"measures": _STR_LIST, "baseline_filters": _OBJ, "comparison_filters": _OBJ},
    ),
]


class ScriptedModel(BaseChatModel):
    script: list[Any]
    delay: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        if self.delay:
            time.sleep(self.delay)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def _ai(text: str = "", tool_calls: list[dict[str, Any]] | None = None) -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=tool_calls or [],
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        response_metadata={"model_name": "taxi-planner", "finish_reason": "stop"},
    )


def _tool(name: str, args: dict[str, Any], call_id: str = "t1") -> AIMessage:
    return _ai(tool_calls=[{"name": name, "args": args, "id": call_id}])


class FakeMCP:
    def __init__(self, result: Any = None, error: MCPToolError | None = None):
        self.result = result or {
            "query_id": "qid_1",
            "row_count": 2,
            "rows": [[1], [2]],
        }
        self.error = error
        self.calls = 0

    def aggregate_taxi_data(self, **kw: Any) -> Any:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class Publisher(InMemoryEventPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.flags: dict[str, str] = {}

    def _get_client(self) -> Publisher:
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.flags[key] = value

    def get(self, key: str) -> str | None:
        return self.flags.get(key)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    clear_tool_definition_cache()
    yield
    clear_tool_definition_cache()


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    script: list[Any],
    *,
    mcp: Any = None,
    budgets: ExecutionBudgets | None = None,
    delay: float = 0.0,
    llm: Any = None,
):
    model = ScriptedModel(script=script, delay=delay)
    real = deep_agents_run.build_deep_agent

    def fake_build(**kwargs: Any) -> Any:
        return real(
            **{
                **kwargs,
                "chat_model": model,
                "list_tools": lambda: DEFINITIONS,
            }
        )

    monkeypatch.setattr(deep_agents_run, "build_deep_agent", fake_build)
    repo = InMemoryStateRepository()
    pub = Publisher()
    loop = OrchestrationLoop(
        llm_client=llm or LiteLLMClient(PROXY),
        mcp_client=mcp or FakeMCP(),
        state_repository=repo,
        event_publisher=pub,
        budgets=budgets or ExecutionBudgets(),
        agent_strategy="deep_agents",
    )
    return loop, repo, pub


def _types(pub: Publisher) -> list[str]:
    return [e.event_type for e in pub.events]


def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = FakeMCP()
    loop, repo, pub = _setup(
        monkeypatch,
        [
            _tool("aggregate_taxi_data", ARGS),
            _ai("There were 2 trips."),
        ],
        mcp=mcp,
    )
    submission = loop.prepare_run("how many trips?")
    result = loop.execute(submission)

    assert result.status == "completed"
    assert result.answer == "There were 2 trips."
    assert mcp.calls == 1
    assert _types(pub) == [
        "run.received",
        "llm.started",
        "llm.completed",
        "tool.requested",
        "tool.started",
        "tool.completed",
        "llm.started",
        "llm.completed",
        "answer.completed",
        "run.completed",
    ]
    assert [s.step_type for s in result.steps] == [
        "llm_proposal",
        "tool_call",
        "llm_final_answer",
    ]
    assert result.query_id == "qid_1"
    assert result.input_tokens == 200 and result.output_tokens == 40
    assert len(result.llm_calls) == 2
    assert result.estimated_cost_usd > 0  # priced via the policy table
    assert result.llm_calls[0].model_alias == "taxi-planner"
    messages = repo.list_messages(submission.conversation_id)
    assert [m.role for m in messages] == ["user", "tool", "assistant"]
    assert messages[-1].content == "There were 2 trips."
    run = repo.get_run(submission.run_id)
    assert run.status == "completed"
    assert run.metadata["strategy"] == "deep_agents"
    assert len(run.metadata["llm_calls"]) == 2
    assert run.metadata["telemetry"]["selected_tool"] == "aggregate_taxi_data"
    assert run.input_tokens == 200
    last = pub.events[-1].payload
    assert last["status"] == "completed" and last["total_tokens"] == 240


def test_tool_error_is_returned_to_model_and_run_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = FakeMCP(error=MCPToolError(False, "sql failed", reason="invalid_request"))
    loop, repo, pub = _setup(
        monkeypatch,
        [
            _tool("aggregate_taxi_data", ARGS),
            _ai("The query failed."),
        ],
        mcp=mcp,
    )
    result = loop.execute(loop.prepare_run("q"))

    assert result.status == "completed"
    assert "tool.failed" in _types(pub)
    assert "tool.completed" not in _types(pub)
    failed = [s for s in result.steps if s.step_type == "tool_call"]
    assert failed[0].status == "failed"


def test_hallucinated_tool_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = FakeMCP()
    loop, repo, pub = _setup(
        monkeypatch,
        [_tool("execute", {"command": "rm -rf /"}), _ai("I cannot do that.")],
        mcp=mcp,
    )
    result = loop.execute(loop.prepare_run("q"))

    assert result.status == "completed"
    assert mcp.calls == 0
    assert "tool.started" not in _types(pub)


def test_max_llm_calls_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, repo, pub = _setup(
        monkeypatch,
        [
            _tool("aggregate_taxi_data", ARGS),
            _ai("never reached"),
        ],
        budgets=ExecutionBudgets(max_llm_calls=1),
    )
    submission = loop.prepare_run("q")
    result = loop.execute(submission)

    assert result.status == "budget_exceeded"
    assert result.failure_code == "budget_exceeded"
    assert "run.budget_exceeded" in _types(pub)
    run = repo.get_run(submission.run_id)
    assert run.status == "budget_exceeded"
    assert run.metadata["reason"] == "max_llm_calls"


def test_repeated_tool_call_is_budget_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = ARGS
    loop, repo, pub = _setup(
        monkeypatch,
        [
            _tool("aggregate_taxi_data", args, "a"),
            _tool("aggregate_taxi_data", args, "b"),
            _ai("x"),
        ],
    )
    result = loop.execute(loop.prepare_run("q"))
    assert result.status == "budget_exceeded"


def test_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, repo, pub = _setup(monkeypatch, [_ai("slow")], delay=2.0)
    submission = loop.prepare_run("q")
    loop.request_cancellation(submission.run_id)
    result = loop.execute(submission)

    assert result.status == "cancelled"
    assert result.failure_code == "cancelled"
    assert "run.cancelled" in _types(pub)
    assert repo.get_run(submission.run_id).status == "cancelled"


def test_cancellation_during_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    loop, repo, pub = _setup(monkeypatch, [_ai("slow")], delay=2.0)
    submission = loop.prepare_run("q")
    threading.Timer(0.3, loop.request_cancellation, [submission.run_id]).start()
    started = time.monotonic()
    result = loop.execute(submission)

    assert result.status == "cancelled"
    assert time.monotonic() - started < 1.5


def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, repo, pub = _setup(
        monkeypatch,
        [_ai("slow")],
        delay=2.0,
        budgets=ExecutionBudgets(timeout_seconds=0.2),
    )
    submission = loop.prepare_run("q")
    result = loop.execute(submission)

    assert result.status == "budget_exceeded"
    assert (
        repo.get_run(submission.run_id)
        .metadata["reason"]
        .startswith("Exceeded execution timeout")
    )


def test_provider_error_maps_to_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", PROXY)
    error = openai.APIStatusError(
        "boom", response=httpx.Response(503, request=request), body=None
    )
    loop, repo, pub = _setup(monkeypatch, [error])
    submission = loop.prepare_run("q")
    with pytest.raises(OrchestrationError) as raised:
        loop.execute(submission)

    assert raised.value.code == "litellm_proxy_http_503"
    assert raised.value.retryable is True
    run = repo.get_run(submission.run_id)
    assert run.status == "failed" and run.failure_code == "litellm_proxy_http_503"
    failed = pub.events[-1]
    assert failed.event_type == "run.failed"
    assert failed.payload["provider_error"]["provider_status"] == 503
    assert run.metadata["llm_calls"][0]["error_code"] == "litellm_proxy_http_503"


def test_connection_error_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    error = openai.APIConnectionError(request=httpx.Request("POST", PROXY))
    loop, repo, pub = _setup(monkeypatch, [error])
    with pytest.raises(OrchestrationError) as raised:
        loop.execute(loop.prepare_run("q"))
    assert raised.value.code == "litellm_proxy_unavailable"


def test_non_litellm_client_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, repo, pub = _setup(monkeypatch, [], llm=LocalFakeLLMClient())
    submission = loop.prepare_run("q")
    with pytest.raises(OrchestrationError) as raised:
        loop.execute(submission)

    assert raised.value.code == "llm_configuration_error"
    assert "LiteLLM" in str(raised.value)
    assert repo.get_run(submission.run_id).status == "failed"


def test_correlation_hooks_match_manual_headers() -> None:
    corr = _Correlation("run_1", "conv_1", "H.1:a")
    corr.current_call_id = "llm_9"
    request = httpx.Request("POST", PROXY)
    corr.on_request(request)

    import json

    assert json.loads(request.headers["x-litellm-spend-logs-metadata"]) == {
        "run_id": "run_1",
        "call_id": "llm_9",
        "attempt": 1,
        "eval_run_id": "H.1:a",
    }
    assert request.headers["x-litellm-tags"] == "eval_run_id:H.1:a"
    assert request.headers["x-litellm-session-id"] == "conv_1"
    response = httpx.Response(200, headers={"x-litellm-response-cost": "0.0123"})
    corr.on_response(response)
    assert corr.last_cost == 0.0123
    corr.on_response(httpx.Response(200))
    assert corr.last_cost is None

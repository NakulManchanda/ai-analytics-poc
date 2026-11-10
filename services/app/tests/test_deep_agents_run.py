from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any

import httpx
import openai
import pytest
from app.events.publisher import InMemoryEventPublisher
from app.llm import LiteLLMClient, LocalFakeLLMClient
from app.mcp_client import MCPToolError
from app.orchestration import deep_agents_run
from app.orchestration.budgets import ExecutionBudgets
from app.orchestration.deep_agents_run import _Aborted, _Correlation
from app.orchestration.deep_agents_strategy import (
    _run_tool,
    clear_tool_definition_cache,
)
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
        "context.reduced",
        "llm.started",
        "llm.completed",
        "answer.completed",
        "run.completed",
    ]
    assert [s.step_type for s in result.steps] == [
        "llm_proposal",
        "tool_call",
        "context_reduced",
        "llm_final_answer",
    ]
    # Graders read tool rows from context.reduced recent_messages (role=tool), as on the
    # manual path; the durable step carries the same working context for event replay.
    (reduced,) = [e for e in pub.events if e.event_type == "context.reduced"]
    (tool_msg,) = reduced.payload["working_context"]["recent_messages"]
    stored = json.loads(tool_msg["content"])
    assert tool_msg["role"] == "tool" and stored["query_id"] == "qid_1"
    assert isinstance(stored["rows"], list) and stored["rows"]
    assert reduced.payload["query_id"] == "qid_1"
    step = next(s for s in result.steps if s.step_type == "context_reduced")
    assert step.query_id == "qid_1"
    assert step.metadata["working_context"] == reduced.payload["working_context"]
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
    assert run.metadata["grounding"] == "applied"
    assert run.metadata["grounding_report"] == {
        "ok": True,
        "ungrounded_numbers": 0,
        "unknown_query_ids": 0,
        "evidence_count": 1,
    }
    assert run.metadata["evidence"] == {
        "tool_calls_succeeded": 1,
        "tool_calls_failed": 0,
        "evidence_backed": True,
    }
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
    run = repo.get_run(result.run_id)
    assert run.metadata["grounding"] == "applied"
    assert run.metadata["evidence"] == {
        "tool_calls_succeeded": 0,
        "tool_calls_failed": 1,
        "evidence_backed": False,
    }


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


def _metadata(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.headers["x-litellm-spend-logs-metadata"])


def test_correlation_hooks_match_manual_headers() -> None:
    corr = _Correlation("run_1", "conv_1", "H.1:a")
    lc = uuid.uuid4()
    corr.register(lc, "llm_9")
    request = httpx.Request("POST", PROXY)
    corr.on_request(request)

    assert _metadata(request) == {
        "run_id": "run_1",
        "call_id": "llm_9",
        "attempt": 1,
        "eval_run_id": "H.1:a",
    }
    assert request.headers["x-litellm-tags"] == "eval_run_id:H.1:a"
    assert request.headers["x-litellm-session-id"] == "conv_1"
    response = httpx.Response(
        200, headers={"x-litellm-response-cost": "0.0123"}, request=request
    )
    corr.on_response(response)
    assert corr.release(lc) == 0.0123
    assert corr.release(lc) is None  # popped


def test_unparseable_cost_is_not_attributed() -> None:
    corr = _Correlation("run_1", "conv_1", None)
    lc = uuid.uuid4()
    corr.register(lc, "llm_1")
    request = httpx.Request("POST", PROXY)
    corr.on_request(request)
    corr.on_response(httpx.Response(200, request=request))
    assert corr.release(lc) is None


def test_request_without_context_gets_no_call_id_and_no_cost() -> None:
    corr = _Correlation("run_1", "conv_1", None)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_metadata(request))
        return httpx.Response(200, headers={"x-litellm-response-cost": "9.9"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [corr.on_request], "response": [corr.on_response]},
    )
    # Another call is registered elsewhere, but not in this thread's context.
    other = uuid.uuid4()
    threading.Thread(target=corr.register, args=(other, "llm_other")).start()
    time.sleep(0.05)
    client.post(PROXY)

    assert seen == [{"run_id": "run_1", "attempt": 1}]
    assert corr._costs == {}
    assert corr.release(other) is None


def test_concurrent_calls_get_their_own_call_id_and_cost() -> None:
    corr = _Correlation("run_1", "conv_1", None)
    costs = {"llm_a": "0.1", "llm_b": "0.2"}
    seen: dict[str, dict[str, Any]] = {}
    gate = threading.Barrier(2)

    def handler(request: httpx.Request) -> httpx.Response:
        call_id = _metadata(request)["call_id"]
        seen[call_id] = _metadata(request)
        gate.wait(timeout=5)  # both requests are in flight together
        return httpx.Response(200, headers={"x-litellm-response-cost": costs[call_id]})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [corr.on_request], "response": [corr.on_response]},
    )
    billed: dict[str, float | None] = {}

    def call(call_id: str) -> None:
        lc = uuid.uuid4()
        corr.register(lc, call_id)
        client.post(PROXY)
        billed[call_id] = corr.release(lc)

    threads = [threading.Thread(target=call, args=(c,)) for c in costs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert set(seen) == {"llm_a", "llm_b"}
    assert billed == {"llm_a": 0.1, "llm_b": 0.2}


def test_hook_refuses_requests_after_abort() -> None:
    corr = _Correlation("run_1", "conv_1", None)
    sent: list[httpx.Request] = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
        event_hooks={"request": [corr.on_request]},
    )
    client.post(PROXY)
    corr.abort.set()
    with pytest.raises(_Aborted):
        client.post(PROXY)
    assert len(sent) == 1


def test_request_timeout_is_bounded_by_remaining_deadline() -> None:
    corr = _Correlation("run_1", "conv_1", None, remaining_seconds=lambda: 0.5)
    timeouts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200)

    client = httpx.Client(
        timeout=60.0,
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [corr.on_request]},
    )
    client.post(PROXY)
    client.post(PROXY, extensions={"timeout": {"read": 0.1}})
    assert timeouts[0] == {"connect": 0.5, "read": 0.5, "write": 0.5, "pool": 0.5}
    assert timeouts[1]["read"] == 0.1 and timeouts[1]["connect"] == 0.5

    spent = _Correlation("run_1", "conv_1", None, remaining_seconds=lambda: 0.0)
    with pytest.raises(_Aborted):
        spent.on_request(httpx.Request("POST", PROXY))


def test_run_tool_checks_abort_before_the_mcp_client() -> None:
    mcp = FakeMCP()

    def refuse() -> None:
        raise _Aborted()

    with pytest.raises(_Aborted):
        _run_tool(mcp, "aggregate_taxi_data", ARGS, refuse)  # type: ignore[arg-type]
    assert mcp.calls == 0
    _run_tool(mcp, "aggregate_taxi_data", ARGS)  # type: ignore[arg-type]
    assert mcp.calls == 1


@pytest.mark.parametrize("how", ["cancel", "timeout"])
def test_abandoned_worker_never_starts_a_tool_after_terminal_state(
    monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    mcp = FakeMCP()
    loop, repo, pub = _setup(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("never")],
        mcp=mcp,
        delay=0.8,  # the model call is still running when the run ends
        budgets=ExecutionBudgets(timeout_seconds=0.2 if how == "timeout" else 60.0),
    )
    submission = loop.prepare_run("q")
    if how == "cancel":
        threading.Timer(0.2, loop.request_cancellation, [submission.run_id]).start()
    result = loop.execute(submission)
    assert result.status == ("cancelled" if how == "cancel" else "budget_exceeded")
    seen = len(pub.events)
    time.sleep(1.2)  # the abandoned model call returns and proposes a tool call

    assert mcp.calls == 0
    assert len(pub.events) == seen  # nothing recorded after the terminal event
    assert pub.events[-1].event_type.startswith("run.")


def test_max_llm_calls_abort_never_reaches_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = FakeMCP()
    loop, repo, pub = _setup(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("never")],
        mcp=mcp,
        budgets=ExecutionBudgets(max_llm_calls=0),
    )
    assert loop.execute(loop.prepare_run("q")).status == "budget_exceeded"
    time.sleep(0.2)
    assert mcp.calls == 0


class HttpModel(BaseChatModel):
    """Issues a real (mock-transported) request through the run's httpx client, so the
    correlation hooks, billing capture and client lifecycle are exercised end to end."""

    script: list[Any]
    client: Any
    errors: list[Any] = []
    metadata_seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "http"

    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        try:
            self.client.post(PROXY + "/v1/chat/completions", json={}).raise_for_status()
        except Exception as err:
            self.errors.append(err)
            raise
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def _setup_http(
    monkeypatch: pytest.MonkeyPatch,
    script: list[Any],
    handler: Any,
    *,
    budgets: ExecutionBudgets | None = None,
    mcp: Any = None,
):
    """Like ``_setup`` but the model uses the run's own httpx client over MockTransport."""
    real_client = httpx.Client
    created: list[Any] = []
    closes: list[float] = []

    class TrackedClient(real_client):  # type: ignore[valid-type,misc]
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, transport=httpx.MockTransport(handler), **kw)
            created.append(self)

        def close(self) -> None:
            closes.append(time.monotonic())
            super().close()

    monkeypatch.setattr(deep_agents_run.httpx, "Client", TrackedClient)
    models: list[HttpModel] = []
    real = deep_agents_run.build_deep_agent

    def fake_build(**kwargs: Any) -> Any:
        model = HttpModel(script=script, client=kwargs["http_client"], errors=[])
        models.append(model)
        return real(
            **{**kwargs, "chat_model": model, "list_tools": lambda: DEFINITIONS}
        )

    monkeypatch.setattr(deep_agents_run, "build_deep_agent", fake_build)
    pub = Publisher()
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(PROXY),
        mcp_client=mcp or FakeMCP(),
        state_repository=InMemoryStateRepository(),
        event_publisher=pub,
        budgets=budgets or ExecutionBudgets(),
        agent_strategy="deep_agents",
    )
    return loop, pub, created, closes, models


def _wait(predicate: Any, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_run_attributes_call_id_and_billed_cost_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metas: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        metas.append(_metadata(request))
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.5"})

    loop, pub, created, closes, models = _setup_http(
        monkeypatch, [_ai("done")], handler
    )
    result = loop.execute(loop.prepare_run("q"))

    assert result.status == "completed"
    call = result.llm_calls[0]
    assert metas[0]["call_id"] == call.llm_call_id
    assert call.cost_usd == 0.5 and call.cost_kind == "billed"
    assert call.cost_source == "provider_billed"
    assert _wait(lambda: created[0].is_closed)  # closed once the worker finished
    assert models[0].errors == []


def test_client_closed_only_after_blocked_request_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = threading.Event()
    in_request = threading.Event()
    returned: list[float] = []
    timeouts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        in_request.set()
        gate.wait(5)  # a slow provider; real transports end at the (bounded) timeout
        returned.append(time.monotonic())
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.5"})

    mcp = FakeMCP()
    loop, pub, created, closes, models = _setup_http(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("never")],
        handler,
        mcp=mcp,
    )
    submission = loop.prepare_run("q")
    threading.Thread(
        target=lambda: (
            in_request.wait(5),
            loop.request_cancellation(submission.run_id),
        )
    ).start()
    result = loop.execute(submission)

    assert result.status == "cancelled"
    assert timeouts[0]["read"] <= 60.0
    assert not created[0].is_closed and closes == []  # request still in flight
    seen = len(pub.events)
    gate.set()
    assert _wait(lambda: created[0].is_closed)
    assert closes[0] >= returned[0]  # closed only after the request returned
    time.sleep(0.2)
    assert models[0].errors == []  # never used after close
    assert mcp.calls == 0
    assert len(pub.events) == seen  # worker recorded nothing after the terminal state


def test_client_closed_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    loop, pub, created, closes, models = _setup_http(monkeypatch, [_ai("x")], handler)
    with pytest.raises(OrchestrationError):
        loop.execute(loop.prepare_run("q"))
    assert _wait(lambda: created[0].is_closed)


def test_client_closed_when_submission_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    loop, pub, created, closes, models = _setup_http(monkeypatch, [_ai("x")], handler)

    class Broken:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        def submit(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("no threads")

        def shutdown(self, *a: Any, **kw: Any) -> None:
            pass

    monkeypatch.setattr(
        deep_agents_run.concurrent.futures, "ThreadPoolExecutor", Broken
    )
    with pytest.raises(RuntimeError):
        loop.execute(loop.prepare_run("q"))
    assert created[0].is_closed


@pytest.mark.skipif(
    not os.environ.get("DEEP_AGENTS_LIVE_LITELLM_URL"),
    reason="live check: set DEEP_AGENTS_LIVE_LITELLM_URL (e.g. http://127.0.0.1:14000)",
)
def test_live_call_id_usage_and_cost_match_proxy_spend_logs() -> None:
    """OPT-IN LIVE CHECK, skipped unless DEEP_AGENTS_LIVE_LITELLM_URL is set (never in CI).

    Needs a running LiteLLM proxy + MCP server (e.g. the local AWS stack) and
    LITELLM_MASTER_KEY (the same key ``make litellm-calls`` uses); MCP_URL optionally
    overrides the MCP endpoint. Runs one question through the deep_agents path, then reads
    the proxy's ``/spend/logs`` (the API behind ``make litellm-calls`` / ``litellm-spend``)
    for this conversation and compares call_id, token usage and billed cost per call.
    It spends real Bedrock tokens, so run it deliberately.
    """
    from app.mcp_client import FastMCPDatasetProfileClient

    url = os.environ["DEEP_AGENTS_LIVE_LITELLM_URL"].rstrip("/")
    key = os.environ["LITELLM_MASTER_KEY"]
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(url, api_key=key),
        mcp_client=FastMCPDatasetProfileClient(os.environ.get("MCP_URL")),
        state_repository=InMemoryStateRepository(),
        event_publisher=Publisher(),
        budgets=ExecutionBudgets(),
        agent_strategy="deep_agents",
    )
    submission = loop.prepare_run("How many taxi trips are in the dataset?")
    result = loop.execute(submission)
    assert result.status == "completed"

    logs = httpx.get(
        f"{url}/spend/logs", headers={"Authorization": f"Bearer {key}"}, timeout=30
    ).json()
    mine = [e for e in logs if e.get("session_id") == submission.conversation_id]
    assert len(mine) == len(result.llm_calls) >= 1
    by_call = {}
    for entry in mine:
        meta = entry.get("metadata") or {}
        meta = meta.get("spend_logs_metadata") or meta
        by_call[meta.get("call_id")] = entry
    for call in result.llm_calls:
        entry = by_call[call.llm_call_id]
        assert entry["prompt_tokens"] == call.input_tokens
        assert entry["completion_tokens"] == call.output_tokens
        assert call.cost_usd == pytest.approx(entry["spend"], rel=1e-6)


def test_fabricated_tool_block_without_a_tool_call_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = '<untrusted_tool_data query_id="query_made_up">{"rows": [[42]]}</untrusted_tool_data>'
    loop, _repo, _pub = _setup(monkeypatch, [_ai(f"Busiest hour is 6pm. {fake}")])
    result = loop.execute(loop.prepare_run("busiest hours for these two?"))

    assert "untrusted_tool_data" not in result.answer
    assert "query_made_up" not in result.answer
    assert "no verified figures" in result.answer
    assert any(
        s.step_type == "grounding_check" and s.status == "failed" for s in result.steps
    )

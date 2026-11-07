"""Deep Agents obey the same model policy as manual LiteLLM calls (#226 P2):
entitlement -> per-call cap -> per-run reservation -> call -> commit/release.
Fallback aliases are intentionally not applied (``fallback_used`` stays False)."""

from __future__ import annotations

import json
import threading
from typing import Any

import httpx
import pytest
from app.llm import LiteLLMClient, LLMProviderError
from app.model_policy import (
    ModelPolicy,
    ModelPolicyRuntime,
    RolePolicy,
    RunBudgets,
    call_context,
    default_pricing,
)
from app.orchestration import deep_agents_run
from app.orchestration.budgets import ExecutionBudgets
from app.orchestration.deep_agents_strategy import clear_tool_definition_cache
from app.orchestration.loop import OrchestrationError, OrchestrationLoop
from app.state.repository import InMemoryStateRepository
from test_deep_agents_run import (
    ARGS,
    DEFINITIONS,
    FakeMCP,
    Publisher,
    _ai,
    _setup,
    _tool,
    _types,
    _wait,
)

PROXY = "http://litellm.test:4000"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    clear_tool_definition_cache()
    yield
    clear_tool_definition_cache()


def _policy(allow: dict[str, frozenset[str]] | None = None, cap: int | None = 300):
    return ModelPolicy(
        roles={
            "planner": RolePolicy("taxi-planner", cap),
            "answer": RolePolicy("taxi-answer", 256),
        },
        providers={"taxi-planner": "bedrock", "taxi-answer": "bedrock"},
        tenant_allowlists=allow or {},
    )


def _runtime(policy: ModelPolicy | None = None, **caps: Any) -> ModelPolicyRuntime:
    policy = policy or _policy()
    return ModelPolicyRuntime(
        policy, default_pricing(tuple(policy.providers)), RunBudgets(**caps)
    )


def _held(rt: ModelPolicyRuntime, run_id: str) -> int:
    return rt.budgets._runs[run_id].held_tokens


def _completion(text: str = "done") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "taxi-planner",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        },
    )


def _setup_real(monkeypatch: pytest.MonkeyPatch, rt: ModelPolicyRuntime, handler: Any):
    """Real ChatOpenAI over the run's httpx client with a MockTransport."""
    real_client = httpx.Client

    class MockClient(real_client):  # type: ignore[valid-type,misc]
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(deep_agents_run.httpx, "Client", MockClient)
    real = deep_agents_run.build_deep_agent
    monkeypatch.setattr(
        deep_agents_run,
        "build_deep_agent",
        lambda **kw: real(**{**kw, "list_tools": lambda: DEFINITIONS}),
    )
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(PROXY, runtime=rt),
        mcp_client=FakeMCP(),
        state_repository=InMemoryStateRepository(),
        event_publisher=Publisher(),
        budgets=ExecutionBudgets(),
        agent_strategy="deep_agents",
    )
    return loop


def test_not_entitled_fails_before_any_model_request(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    rt = _runtime(_policy(allow={"someone-else": frozenset({"taxi-planner"})}))
    loop = _setup_real(monkeypatch, rt, lambda r: requests.append(r) or _completion())
    with pytest.raises(OrchestrationError) as err:
        loop.execute(loop.prepare_run("q"))
    assert err.value.code == "model_not_entitled" and not err.value.retryable
    assert requests == []
    types = _types(loop._publisher)
    assert types[-1] == "run.failed" and "llm.started" not in types


def test_per_call_cap_forwarded_and_recorded(monkeypatch) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _completion()

    rt = _runtime(_policy(cap=300))
    loop = _setup_real(monkeypatch, rt, handler)
    result = loop.execute(loop.prepare_run("q"))
    assert result.status == "completed"
    # the cap goes out as ``max_tokens``, the field the manual LiteLLM payload sends
    assert (
        bodies[0]["max_tokens"] == 300
        and "max_completion_tokens" not in bodies[0]
        and bodies[0]["model"] == "taxi-planner"
    )
    call = result.llm_calls[0]
    assert call.configured_max_tokens == 300
    assert call.fallback_used is False and call.model_alias == "taxi-planner"


def test_no_cap_means_no_max_tokens_in_request(monkeypatch) -> None:
    bodies: list[dict[str, Any]] = []
    loop = _setup_real(
        monkeypatch,
        _runtime(_policy(cap=None)),
        lambda r: bodies.append(json.loads(r.content)) or _completion(),
    )
    assert loop.execute(loop.prepare_run("q")).status == "completed"
    assert "max_tokens" not in bodies[0] and "max_completion_tokens" not in bodies[0]


def test_run_budget_too_small_fails_before_request(monkeypatch) -> None:
    requests: list[Any] = []
    rt = _runtime(max_tokens=10)
    loop = _setup_real(monkeypatch, rt, lambda r: requests.append(r) or _completion())
    submission = loop.prepare_run("q")
    with pytest.raises(OrchestrationError) as err:
        loop.execute(submission)
    assert err.value.code == "run_budget_exhausted" and not err.value.retryable
    assert requests == []
    assert _types(loop._publisher)[-1] == "run.failed"


def test_reservation_committed_with_actual_usage(monkeypatch) -> None:
    rt = _runtime(max_tokens=100_000)
    loop = _setup_real(monkeypatch, rt, lambda r: _completion())
    submission = loop.prepare_run("q")
    assert loop.execute(submission).status == "completed"
    summary = rt.budgets.summary(submission.run_id)
    assert summary["calls"] == 1
    assert (summary["input_tokens"], summary["output_tokens"]) == (100, 20)
    assert summary["budget_tokens_used"] == 120
    assert _held(rt, submission.run_id) == 0


def test_reservation_released_on_provider_error(monkeypatch) -> None:
    rt = _runtime(max_tokens=100_000)
    loop = _setup_real(monkeypatch, rt, lambda r: httpx.Response(503))
    submission = loop.prepare_run("q")
    with pytest.raises(OrchestrationError):
        loop.execute(submission)
    assert rt.budgets.summary(submission.run_id)["calls"] == 0
    assert _held(rt, submission.run_id) == 0


def test_reservation_released_on_cancellation() -> None:
    rt = _runtime(max_tokens=100_000)
    llm = LiteLLMClient(PROXY, runtime=rt)
    import pytest as _p

    mp = _p.MonkeyPatch()
    try:
        loop, repo, pub = _setup(mp, [_ai("slow")], delay=1.0, llm=llm)
        submission = loop.prepare_run("q")
        seen: list[int] = []

        def cancel() -> None:
            _wait(
                lambda: submission.run_id in rt.budgets._runs
                and rt.budgets._runs[submission.run_id].held_tokens > 0
            )
            seen.append(rt.budgets._runs[submission.run_id].held_tokens)
            loop.request_cancellation(submission.run_id)

        threading.Thread(target=cancel).start()
        result = loop.execute(submission)
    finally:
        mp.undo()
    assert result.status == "cancelled"
    assert seen and seen[0] > 0  # a hold existed while the call was open
    assert _held(rt, submission.run_id) == 0


def test_reservation_released_when_run_aborts_on_loop_budget(monkeypatch) -> None:
    rt = _runtime(max_tokens=100_000)
    loop, repo, pub = _setup(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("never")],
        budgets=ExecutionBudgets(max_llm_calls=1),
        llm=LiteLLMClient(PROXY, runtime=rt),
    )
    submission = loop.prepare_run("q")
    assert loop.execute(submission).status == "budget_exceeded"
    assert rt.budgets.summary(submission.run_id)["calls"] == 1
    assert _held(rt, submission.run_id) == 0


def test_sequential_calls_share_the_run_budget(monkeypatch) -> None:
    sizes: list[int] = []

    def probe_run() -> None:
        rt = _runtime(max_tokens=1_000_000)
        real_reserve = rt.budgets.reserve

        def spy(run_id: str, tokens: int, usd: Any) -> Any:
            sizes.append(tokens)
            return real_reserve(run_id, tokens, usd)

        rt.budgets.reserve = spy  # type: ignore[method-assign]
        with pytest.MonkeyPatch.context() as mp:  # _setup stacks patches; undo it
            loop, _, _ = _setup(
                mp,
                [_tool("aggregate_taxi_data", ARGS), _ai("ok")],
                llm=LiteLLMClient(PROXY, runtime=rt),
            )
            assert loop.execute(loop.prepare_run("q")).status == "completed"

    probe_run()
    first, second = sizes
    assert second > first
    # First call fits (and consumes 120); the second no longer does.
    rt = _runtime(max_tokens=120 + second - 1)
    loop, _, pub = _setup(
        monkeypatch,
        [_tool("aggregate_taxi_data", ARGS), _ai("never")],
        llm=LiteLLMClient(PROXY, runtime=rt),
    )
    with pytest.raises(OrchestrationError) as err:
        loop.execute(loop.prepare_run("q"))
    assert err.value.code == "run_budget_exhausted", str(err.value)
    assert _types(pub).count("llm.started") == 1


# -- LiteLLMClient agent-call methods ------------------------------------------------
def test_resolve_agent_role_returns_alias_and_cap_or_denies() -> None:
    client = LiteLLMClient(PROXY, runtime=_runtime(_policy(cap=77)))
    assert client.resolve_agent_role() == ("taxi-planner", 77)
    denied = LiteLLMClient(
        PROXY, runtime=_runtime(_policy(allow={"x": frozenset({"taxi-planner"})}))
    )
    with pytest.raises(LLMProviderError) as err:
        denied.resolve_agent_role()
    assert err.value.code == "model_not_entitled" and not err.value.retryable


def test_begin_agent_call_without_run_does_not_reserve() -> None:
    rt = _runtime(max_tokens=1)
    res = LiteLLMClient(PROXY, runtime=rt).begin_agent_call("hello")
    assert res.reservation is None and res.alias == "taxi-planner"
    assert rt.budgets._runs == {}


def test_begin_end_release_agent_call_account_like_governed() -> None:
    rt = _runtime(max_tokens=2000)
    client = LiteLLMClient(PROXY, runtime=rt)
    with call_context("default", "r1"):
        res = client.begin_agent_call("x" * 40)
        assert res.reservation is not None and _held(rt, "r1") > 0
        client.end_agent_call(
            res, input_tokens=10, output_tokens=5, usage_known=True, cost_usd=0.01
        )
        client.end_agent_call(
            res, input_tokens=10, output_tokens=5, usage_known=True, cost_usd=0.01
        )  # second settle is a no-op
        summary = rt.budgets.summary("r1")
        assert summary["calls"] == 1 and summary["budget_tokens_used"] == 15
        assert _held(rt, "r1") == 0

        unknown = client.begin_agent_call("x")
        client.end_agent_call(
            unknown, input_tokens=0, output_tokens=0, usage_known=False, cost_usd=None
        )
        assert rt.budgets.summary("r1")["incomplete"] is True

        released = client.begin_agent_call("x")
        client.release_agent_call(released)
        client.release_agent_call(released)  # idempotent
        client.release_agent_call(None)
        assert _held(rt, "r1") == 0
        assert rt.budgets.summary("r1")["calls"] == 2


def test_begin_agent_call_run_budget_exhausted() -> None:
    client = LiteLLMClient(PROXY, runtime=_runtime(max_tokens=5))
    with call_context("default", "r2"), pytest.raises(LLMProviderError) as err:
        client.begin_agent_call("x")
    assert err.value.code == "run_budget_exhausted" and not err.value.retryable

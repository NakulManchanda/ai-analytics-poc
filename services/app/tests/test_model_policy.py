"""Model-role policy (#170): budgets, concurrency, retries, entitlement, usage, fallbacks."""

import json
import threading

import httpx
import pytest
from app.llm import LiteLLMClient, LLMProviderError
from app.model_policy import (
    ModelPolicy,
    ModelPolicyRuntime,
    PolicyConfigError,
    RolePolicy,
    RunBudgetExceededError,
    RunBudgets,
    call_context,
    default_pricing,
)

PROXY = "http://litellm:4000"
USAGE = {"prompt_tokens": 100, "completion_tokens": 50}


def _ok(usage=USAGE, headers=None, model="taxi-answer"):
    body = {
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body, headers=headers or {})


def _client(handler, runtime=None, **kw) -> LiteLLMClient:
    return LiteLLMClient(
        PROXY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        runtime=runtime,
        **kw,
    )


def _runtime(policy=None, **caps) -> ModelPolicyRuntime:
    policy = policy or ModelPolicy.default("taxi-planner", "taxi-answer")
    return ModelPolicyRuntime(
        policy, default_pricing(tuple(policy.providers)), RunBudgets(**caps)
    )


def _policy(fallbacks=(), allow=None, providers=None) -> ModelPolicy:
    return ModelPolicy(
        roles={
            "planner": RolePolicy("taxi-planner", 512),
            "answer": RolePolicy("taxi-answer", 256, tuple(fallbacks)),
        },
        providers=providers
        or {"taxi-planner": "bedrock", "taxi-answer": "bedrock", "alt": "bedrock"},
        tenant_allowlists=allow or {},
    )


def test_result_carries_role_alias_pricing_and_estimated_cost() -> None:
    result = _client(lambda r: _ok()).ask("x")
    assert (result.role, result.model_alias, result.cost_kind) == (
        "answer",
        "taxi-answer",
        "estimated",
    )
    assert result.pricing_version
    assert result.cost_usd == pytest.approx((100 * 0.035 + 50 * 0.140) / 1e6)
    assert result.usage_known and not result.fallback_used


def test_billed_cost_header_wins_over_estimate() -> None:
    result = _client(lambda r: _ok(headers={"x-litellm-response-cost": "0.00042"})).ask(
        "x"
    )
    assert (result.cost_usd, result.cost_kind) == (0.00042, "billed")


def test_missing_usage_is_flagged_and_cost_unknown_not_zero() -> None:
    runtime = _runtime(max_tokens=10_000)
    with call_context("t", "run1"):
        result = _client(lambda r: _ok(usage=None), runtime).ask("x")
    assert not result.usage_known
    assert result.cost_usd is None and result.cost_kind == "unknown"
    summary = runtime.budgets.summary("run1")
    assert summary["incomplete"] is True
    assert summary["input_tokens"] == 0  # unknown usage is not counted as a real 0 call


def test_swap_planner_and_answer_model_by_config(tmp_path) -> None:
    seen: list[str] = []

    def handler(request):
        seen.append(json.loads(request.content)["model"])
        return _ok()

    cfg = tmp_path / "policy.json"
    cfg.write_text(
        json.dumps(
            {
                "providers": {"fast": "bedrock", "smart": "bedrock"},
                "roles": {"planner": {"alias": "smart"}, "answer": {"alias": "fast"}},
            }
        )
    )
    runtime = ModelPolicyRuntime.default("x", "y", policy_file=str(cfg))
    client = _client(handler, runtime)
    client._post_completion({"messages": [], "tools": [{}]}, {})
    client.ask("q")
    assert seen == ["smart", "fast"]


def test_per_call_max_tokens_is_capped_by_role() -> None:
    sent: list[int] = []

    def handler(request):
        sent.append(json.loads(request.content)["max_tokens"])
        return _ok()

    client = _client(handler, _runtime(_policy()))
    client._post_completion({"messages": [], "max_tokens": 9999}, {})
    assert sent == [256]


def test_entitlement_denial_makes_no_request() -> None:
    calls: list[int] = []
    policy = _policy(allow={"acme": frozenset({"taxi-planner"})})
    client = _client(lambda r: calls.append(1) or _ok(), _runtime(policy))
    with call_context("acme", "r"), pytest.raises(LLMProviderError) as err:
        client.ask("x")
    assert err.value.code == "model_not_entitled" and not err.value.retryable
    assert calls == []


def test_run_budget_exhausts_after_cumulative_spend() -> None:
    calls: list[int] = []
    client = _client(lambda r: calls.append(1) or _ok(), _runtime(max_tokens=1300))
    with call_context("t", "r1"):
        client.ask("x")
        client.ask(
            "x"
        )  # 300 used, a third worst-case reservation (~1030) no longer fits
        with pytest.raises(LLMProviderError) as err:
            client.ask("x")
    assert err.value.code == "run_budget_exhausted" and len(calls) == 2


def test_run_budget_exhausted_code_and_no_request() -> None:
    calls: list[int] = []
    client = _client(lambda r: calls.append(1) or _ok(), _runtime(max_tokens=10))
    with call_context("t", "r2"), pytest.raises(LLMProviderError) as err:
        client.ask("x")
    assert err.value.code == "run_budget_exhausted" and not err.value.retryable
    assert calls == []


def test_concurrent_reservations_never_overshoot() -> None:
    budgets = RunBudgets(max_tokens=1000)
    granted: list[int] = []
    lock = threading.Lock()

    def worker():
        try:
            budgets.reserve("r", 100, None)
            with lock:
                granted.append(1)
        except RunBudgetExceededError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(granted) == 10


def test_retry_after_failure_is_not_double_charged() -> None:
    responses = iter([httpx.Response(503), _ok()])
    runtime = _runtime(max_tokens=100_000)
    client = _client(lambda r: next(responses), runtime)
    with call_context("t", "r3"):
        with pytest.raises(LLMProviderError) as err:
            client.ask("x")
        assert err.value.retryable
        client.ask("x")  # the app-level retry
    summary = runtime.budgets.summary("r3")
    assert summary["budget_tokens_used"] == 150 and summary["calls"] == 1
    assert runtime.budgets._runs["r3"].held_tokens == 0


def test_fallback_used_when_allowlisted() -> None:
    seen: list[str] = []

    def handler(request):
        alias = json.loads(request.content)["model"]
        seen.append(alias)
        return httpx.Response(503) if alias == "taxi-answer" else _ok(model=alias)

    result = _client(handler, _runtime(_policy(fallbacks=["alt"]))).ask("x")
    assert seen == ["taxi-answer", "alt"]
    assert result.fallback_used and result.model_alias == "alt"


def test_rejected_fallback_is_never_called() -> None:
    seen: list[str] = []

    def handler(request):
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(503)

    policy = _policy(
        fallbacks=["alt"], allow={"acme": frozenset({"taxi-planner", "taxi-answer"})}
    )
    with call_context("acme", None), pytest.raises(LLMProviderError) as err:
        _client(handler, _runtime(policy)).ask("x")
    assert seen == ["taxi-answer"] and err.value.retryable


def test_cross_provider_fallback_rejected_at_config_load() -> None:
    with pytest.raises(PolicyConfigError, match="crosses providers"):
        _policy(
            fallbacks=["openai-x"],
            providers={
                "taxi-planner": "bedrock",
                "taxi-answer": "bedrock",
                "openai-x": "openai",
            },
        )


def test_monthly_guard_still_authoritative() -> None:
    from app.bedrock_budget import BedrockBudgetExceededError

    class Deny:
        def reserve(self, model_id):
            raise BedrockBudgetExceededError("x")

    calls: list[int] = []
    client = _client(lambda r: calls.append(1) or _ok(), budget=Deny())
    with pytest.raises(LLMProviderError) as err:
        client.ask("x")
    assert err.value.code == "bedrock_budget_exhausted" and calls == []

"""#186 slice A: classify Bedrock 'invalid ToolUse sequence' as a typed, non-retried
provider failure with safe structured details, null usage and conservative budget."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any

import httpx
import pytest
from app.config import VoiceSettings
from app.events import InMemoryEventPublisher
from app.llm import LiteLLMClient, LLMProviderError, ServeLLMClient
from app.model_policy import (
    ModelPolicy,
    ModelPolicyRuntime,
    PricingTable,
    RolePolicy,
    RunBudgets,
    default_pricing,
)
from app.orchestration import OrchestrationError, OrchestrationLoop
from app.orchestration.budgets import ExecutionBudgets
from app.state import InMemoryStateRepository
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from test_orchestration_loop import FakeMCPClient

PROXY = "http://litellm:4000"
MARKER = "Model produced invalid sequence as part of ToolUse"
SECRET_BODY = (
    'BedrockException - {"message":"' + MARKER + ' SECRET-PROMPT-TEXT-48151623"}'
)
QUESTION = "Which pickup zones are busiest in this month of taxi trips?"
SCHEMA = {"dataset": "nyc-taxi", "month": "2024-01", "columns": ["PULocationID"]}


def _error_body(text: str = SECRET_BODY) -> dict[str, Any]:
    return {"error": {"message": text, "type": "None", "code": "424"}}


def _client(status=424, text=SECRET_BODY, headers=None, runtime=None, fallbacks=False):
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(status, json=_error_body(text), headers=headers or {})

    client = LiteLLMClient(
        PROXY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        runtime=runtime,
    )
    return client, seen


def _propose(client: LiteLLMClient) -> LLMProviderError:
    with pytest.raises(LLMProviderError) as exc:
        client.propose_taxi_query(QUESTION, SCHEMA)
    return exc.value


def test_planner_424_with_marker_is_classified() -> None:
    client, seen = _client(headers={"x-litellm-call-id": "call-abc"})
    err = _propose(client)
    assert err.code == "malformed_tool_use"
    assert err.retryable is False
    assert err.provider_status == 424
    assert err.provider_call_id == "call-abc"
    assert err.attempts == 1
    assert err.model_id == "taxi-planner"
    assert err.input_tokens is None and err.output_tokens is None
    assert len(seen) == 1  # exactly one request: no retry


def test_error_never_carries_body_text() -> None:
    err = _propose(_client()[0])
    assert str(err) == ""
    assert "SECRET-PROMPT-TEXT" not in repr(vars(err))
    assert err.provider_call_id is None  # header absent


def test_400_with_marker_is_also_classified() -> None:
    err = _propose(_client(status=400)[0])
    assert err.code == "malformed_tool_use" and err.provider_status == 400


@pytest.mark.parametrize(
    ("status", "text", "code", "retryable"),
    [
        (
            424,
            "BedrockException - something else entirely",
            "litellm_proxy_http_424",
            False,
        ),
        (424, "invalid sequence as part of ToolUse", "litellm_proxy_http_424", False),
        (503, MARKER, "litellm_proxy_http_503", True),
        (500, MARKER, "litellm_proxy_http_500", False),
        (429, "rate limited", "litellm_proxy_http_429", False),
    ],
)
def test_other_failures_unchanged(status, text, code, retryable) -> None:
    err = _propose(_client(status=status, text=text)[0])
    assert (err.code, err.retryable) == (code, retryable)
    assert err.provider_status is None and err.attempts is None


def test_marker_beyond_peek_window_is_not_matched() -> None:
    err = _propose(_client(text="x" * 3000 + MARKER)[0])
    assert err.code == "litellm_proxy_http_424"


def test_non_planner_payload_with_marker_is_not_classified() -> None:
    client, _ = _client()
    with pytest.raises(LLMProviderError) as exc:
        client.ask("hello")
    assert exc.value.code == "litellm_proxy_http_424"


def test_governed_alias_loop_does_not_fall_back_for_this_error() -> None:
    policy = ModelPolicy(
        roles={
            "planner": RolePolicy("taxi-planner", 512, ("alt-planner",)),
            "answer": RolePolicy("taxi-answer", 256),
        },
        providers={
            "taxi-planner": "bedrock",
            "taxi-answer": "bedrock",
            "alt-planner": "bedrock",
        },
        tenant_allowlists={},
    )
    runtime = ModelPolicyRuntime(
        policy, default_pricing(tuple(policy.providers)), RunBudgets(max_tokens=10**6)
    )
    client, seen = _client(runtime=runtime)
    err = _propose(client)
    assert err.code == "malformed_tool_use"
    assert [s["model"] for s in seen] == ["taxi-planner"]  # no fallback alias tried


def test_followup_planner_payload_is_one_user_message() -> None:
    client, seen = _client()
    _propose(client)
    messages = seen[0]["messages"]
    assert [m["role"] for m in messages] == ["user"]
    assert seen[0]["tools"] and seen[0]["temperature"] == 0.0


# --- loop integration -----------------------------------------------------------


def _loop(runtime=None, publisher=None, tracer=None):
    client, seen = _client(
        headers={"x-litellm-call-id": "call-xyz"},
        runtime=runtime,
    )
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=client,
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
        voice_settings=VoiceSettings(enabled=False),
        **({"tracer": tracer} if tracer else {}),
    )
    return loop, repo, seen


def _runtime() -> ModelPolicyRuntime:
    policy = ModelPolicy.default("taxi-planner", "taxi-answer")
    return ModelPolicyRuntime(
        policy, default_pricing(tuple(policy.providers)), RunBudgets(max_tokens=10**6)
    )


def test_run_failed_payload_and_metadata_carry_provider_error() -> None:
    publisher = InMemoryEventPublisher()
    loop, repo, seen = _loop(_runtime(), publisher)
    with pytest.raises(OrchestrationError) as exc:
        loop.run(QUESTION)
    assert exc.value.code == "malformed_tool_use"
    assert len(seen) == 1
    run_id = next(iter(repo._runs))
    expected = {
        "category": "malformed_tool_use",
        "provider_status": 424,
        "provider_call_id": "call-xyz",
        "attempts": 1,
    }
    failed = [
        e for e in publisher.get_events_for_run(run_id) if e.event_type == "run.failed"
    ]
    assert len(failed) == 1
    assert failed[0].payload["failure_code"] == "malformed_tool_use"
    assert failed[0].payload["provider_error"] == expected
    run = repo.get_run(run_id)
    assert run is not None and run.failure_code == "malformed_tool_use"
    assert run.metadata["provider_error"] == expected
    assert "SECRET-PROMPT-TEXT" not in json.dumps(
        [e.payload for e in publisher.get_events_for_run(run_id)], default=str
    ) + json.dumps(run.metadata, default=str)


def test_failed_call_has_null_usage_and_cost_but_budget_is_charged() -> None:
    runtime = _runtime()
    loop, repo, _ = _loop(runtime)
    with pytest.raises(OrchestrationError):
        loop.run(QUESTION)
    run_id = next(iter(repo._runs))
    run = repo.get_run(run_id)
    (call,) = run.metadata["llm_calls"]
    assert call["error_code"] == "malformed_tool_use"
    assert call["input_tokens"] is None and call["output_tokens"] is None
    assert call["cost_usd"] is None
    assert call["usage_known"] is False
    assert call["model_alias"] == "taxi-planner"
    assert call["latency_ms"] >= 0
    assert call["provider_status"] == 424
    # Unknown is not free: the per-run budget is charged the conservative bound.
    assert call["budget_cost_basis"] == "conservative_bound"
    assert call["cost_indeterminate"] is True
    assert call["budget_cost_usd"] and call["budget_cost_usd"] > 0
    # Per-run reservation held for the request was released, never committed.
    summary = runtime.budgets.summary(run_id)
    assert summary["calls"] == 0 and summary["budget_tokens_used"] == 0
    # Reported run totals do not include invented tokens/cost.
    assert run.input_tokens == 0 and run.output_tokens == 0
    assert run.estimated_cost_usd == 0.0


def test_tiny_run_cap_does_not_mask_malformed_code() -> None:
    """The conservative charge exceeds this cap; the provider failure code must win."""
    loop, repo, _ = _loop(_runtime())
    with pytest.raises(OrchestrationError) as exc:
        loop.execute(
            loop.prepare_run(QUESTION),
            budgets=ExecutionBudgets(max_estimated_cost_usd=1e-12),
        )
    assert exc.value.code == "malformed_tool_use"
    run = repo.get_run(next(iter(repo._runs)))
    (call,) = run.metadata["llm_calls"]
    assert call["budget_cost_usd"] > 1e-12  # the charge was applied, not skipped


def test_conservative_charge_keeps_later_calls_determinate() -> None:
    """A priced alias yields a bound (not unresolvable), so ensure_cost_determinate
    would not refuse a later call; only the failing run's own accounting changes."""
    loop, repo, _ = _loop(_runtime())
    with pytest.raises(OrchestrationError):
        loop.run(QUESTION)
    run = repo.get_run(next(iter(repo._runs)))
    (call,) = run.metadata["llm_calls"]
    assert (
        call["budget_cost_usd"] is not None
    )  # priced -> cost_unresolvable stays False


def test_otel_attributes_on_llm_call_event_and_run_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    loop, _, _ = _loop(_runtime(), tracer=provider.get_tracer("t"))
    with pytest.raises(OrchestrationError):
        loop.run(QUESTION)
    (span,) = exporter.get_finished_spans()
    assert span.attributes["ai.error.category"] == "malformed_tool_use"
    (event,) = [e for e in span.events if e.name == "ai.llm_call"]
    assert event.attributes["ai.llm.error_category"] == "malformed_tool_use"
    assert event.attributes["ai.llm.provider_status"] == 424
    assert "SECRET-PROMPT" not in repr(span.attributes) + repr(event.attributes)


def test_serve_client_planner_also_classified_and_single_request() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(424, text=SECRET_BODY)

    client = ServeLLMClient(
        gateway_url="http://gw/serve",
        model_id="m",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    err = _propose(client)  # type: ignore[arg-type]
    assert err.code == "malformed_tool_use" and err.model_id == "m"
    assert len(seen) == 1


def test_unpriced_alias_failed_call_is_unresolvable_not_free() -> None:
    """No price for the alias: the failed attempt's budget cost is unresolvable (fail
    closed), never 0, and the provider failure code is still reported."""
    policy = ModelPolicy.default("taxi-planner", "taxi-answer")
    runtime = ModelPolicyRuntime(
        policy,
        PricingTable("test-empty", MappingProxyType({})),
        RunBudgets(max_tokens=10**6),
    )
    loop, repo, _ = _loop(runtime)
    with pytest.raises(OrchestrationError) as exc:
        loop.run(QUESTION)
    assert exc.value.code == "malformed_tool_use"
    run = repo.get_run(next(iter(repo._runs)))
    (call,) = run.metadata["llm_calls"]
    assert call["input_tokens"] is None and call["cost_usd"] is None
    assert call["budget_cost_usd"] is None
    assert call["budget_cost_basis"] == "unresolvable"

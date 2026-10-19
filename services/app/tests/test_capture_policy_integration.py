"""#176: policy context bound in the loop; capture records what LLMResult reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.capture.recorder import CaptureRecorder, CaptureSettings
from app.llm import LiteLLMClient
from app.model_policy import (
    ModelPolicy,
    ModelPolicyRuntime,
    RunBudgets,
    default_pricing,
)
from app.orchestration import OrchestrationError, OrchestrationLoop
from app.state import InMemoryStateRepository
from test_orchestration_loop import FakeMCPClient

PROXY = "http://litellm:4000"
QUESTION = (
    "Which pickup zones are busiest in this month of taxi trips?"  # not in catalogue
)


def _handler(usage: dict[str, int] | None, calls: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        message: dict[str, Any] = {"role": "assistant", "content": "JFK Airport leads."}
        if body.get("tools"):
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "query_taxi_data",
                            "arguments": '{"analysis": "top_pickup_zones", "limit": 5}',
                        }
                    }
                ],
            }
        payload: dict[str, Any] = {
            "model": body["model"],
            "choices": [{"message": message, "finish_reason": "stop"}],
        }
        if usage is not None:
            payload["usage"] = usage
        return httpx.Response(200, json=payload)

    return handler


def _loop(runtime: ModelPolicyRuntime, usage, calls, capture=None):
    client = LiteLLMClient(
        PROXY,
        http_client=httpx.Client(transport=httpx.MockTransport(_handler(usage, calls))),
        runtime=runtime,
    )
    return OrchestrationLoop(
        llm_client=client,
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=capture,
    )


def _runtime(**caps: Any) -> ModelPolicyRuntime:
    policy = ModelPolicy.default("taxi-planner", "taxi-answer")
    return ModelPolicyRuntime(
        policy, default_pricing(tuple(policy.providers)), RunBudgets(**caps)
    )


def test_loop_binds_run_context_so_budget_is_tracked() -> None:
    runtime = _runtime(max_tokens=1_000_000)
    calls: list[dict[str, Any]] = []
    result = _loop(runtime, {"prompt_tokens": 100, "completion_tokens": 20}, calls).run(
        QUESTION
    )
    assert result.status == "completed"
    summary = runtime.budgets.summary(result.run_id)
    assert summary["calls"] == 2 and summary["input_tokens"] == 200


def test_per_run_budget_exhaustion_through_real_run(tmp_path: Path) -> None:
    runtime = _runtime(max_tokens=1000)
    calls: list[dict[str, Any]] = []
    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1")
    loop = _loop(
        runtime, {"prompt_tokens": 100, "completion_tokens": 20}, calls, recorder
    )
    with pytest.raises(OrchestrationError) as err:
        loop.run(QUESTION)
    assert err.value.code == "run_budget_exhausted" and not err.value.retryable
    assert (
        len(calls) == 1
    )  # planner ran; the answer call was refused before any request
    (run_id,) = runtime.budgets._runs  # the loop bound exactly this run's identity
    run = loop._repo.get_run(run_id)
    assert run is not None and run.status == "failed"
    assert run.failure_code == "run_budget_exhausted"
    lines = (tmp_path / "t1" / "captures.jsonl").read_text().splitlines()
    assert {json.loads(x)["run_status"] for x in lines} == {"failed"}


def test_budget_is_per_run_not_shared_between_runs() -> None:
    runtime = _runtime(max_tokens=1_000_000)
    calls: list[dict[str, Any]] = []
    loop = _loop(runtime, {"prompt_tokens": 1, "completion_tokens": 1}, calls)
    a, b = loop.run(QUESTION), loop.run(QUESTION)
    assert a.run_id != b.run_id
    assert runtime.budgets.summary(a.run_id)["calls"] == 2
    assert runtime.budgets.summary(b.run_id)["calls"] == 2


def _captured(tmp_path: Path, usage) -> list[dict[str, Any]]:
    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1")
    _loop(_runtime(), usage, [], capture=recorder).run(QUESTION)
    lines = (tmp_path / "t1" / "captures.jsonl").read_text().splitlines()
    return [r for r in map(json.loads, lines) if r["record_type"] == "model_call"]


def test_capture_records_policy_fields_from_llm_result(tmp_path: Path) -> None:
    planner, answer = _captured(
        tmp_path, {"prompt_tokens": 100, "completion_tokens": 20}
    )
    assert (planner["role"], answer["role"]) == ("planner", "answer")
    assert planner["model_alias"] == "taxi-planner"
    assert answer["model_alias"] == "taxi-answer"
    assert answer["input_tokens"] == 100 and answer["output_tokens"] == 20
    assert answer["pricing_version"]
    assert answer["cost_kind"] in ("estimated", "billed")
    assert answer["cost_usd"] is not None and answer["fallback_used"] is False


def test_capture_tokens_are_null_when_usage_unknown(tmp_path: Path) -> None:
    records = _captured(tmp_path, None)
    for rec in records:
        assert rec["input_tokens"] is None and rec["output_tokens"] is None
        assert rec["reasoning_tokens"] is None
        assert rec["cost_usd"] is None and rec["cost_kind"] == "unknown"


def test_zero_reported_tokens_are_kept_when_usage_known(tmp_path: Path) -> None:
    (planner, _answer) = _captured(
        tmp_path, {"prompt_tokens": 7, "completion_tokens": 0}
    )
    assert planner["input_tokens"] == 7 and planner["output_tokens"] == 0

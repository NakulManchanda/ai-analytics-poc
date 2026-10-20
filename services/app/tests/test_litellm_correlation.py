"""#189 PR-C: proxy spend rows map 1:1 to app run_id / capture call_id via headers."""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
from pathlib import Path
from typing import Any

import httpx
from app.capture.recorder import CaptureRecorder, CaptureSettings
from app.llm import LiteLLMClient
from app.model_policy import (
    ModelPolicy,
    ModelPolicyRuntime,
    RolePolicy,
    RunBudgets,
    call_context,
    default_pricing,
)
from app.model_policy.context import current_llm_call_id, llm_call_scope
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository
from test_orchestration_loop import FakeMCPClient

PROXY = "http://litellm:4000"
QUESTION = "Which pickup zones are busiest in this month of taxi trips?"


def _ok(body: dict[str, Any]) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": "hi"}
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
    return httpx.Response(
        200,
        json={
            "model": body["model"],
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )


def _client(seen: list[dict[str, str]], runtime=None, status=None) -> LiteLLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(dict(request.headers))
        if status is not None and body["model"] == "taxi-answer":
            return httpx.Response(status)
        return _ok(body)

    return LiteLLMClient(
        PROXY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        runtime=runtime,
    )


def _meta(headers: dict[str, str]) -> dict[str, Any]:
    return json.loads(headers["x-litellm-spend-logs-metadata"])


def test_eval_run_gets_tags_and_metadata() -> None:
    seen: list[dict[str, str]] = []
    with call_context("default", "run_1", "H.1:a"), llm_call_scope("llm_1"):
        _client(seen).ask("x")
    (h,) = seen
    assert h["x-litellm-tags"] == "eval_run_id:H.1:a"
    assert _meta(h) == {
        "run_id": "run_1",
        "call_id": "llm_1",
        "attempt": 1,
        "eval_run_id": "H.1:a",
    }


def test_non_eval_run_has_metadata_but_no_tags() -> None:
    seen: list[dict[str, str]] = []
    with call_context("default", "run_1"), llm_call_scope("llm_1"):
        _client(seen).ask("x")
    (h,) = seen
    assert "x-litellm-tags" not in h
    assert _meta(h) == {"run_id": "run_1", "call_id": "llm_1", "attempt": 1}


def test_no_context_sends_no_correlation_headers() -> None:
    seen: list[dict[str, str]] = []
    _client(seen).ask("x")
    (h,) = seen
    assert "x-litellm-spend-logs-metadata" not in h and "x-litellm-tags" not in h


def test_two_physical_attempts_get_distinct_attempt_numbers() -> None:
    policy = ModelPolicy(
        roles={
            "planner": RolePolicy("taxi-planner", 512),
            "answer": RolePolicy("taxi-answer", 256, ("alt",)),
        },
        providers={
            "taxi-planner": "bedrock",
            "taxi-answer": "bedrock",
            "alt": "bedrock",
        },
        tenant_allowlists={},
    )
    runtime = ModelPolicyRuntime(
        policy, default_pricing(tuple(policy.providers)), RunBudgets()
    )
    seen: list[dict[str, str]] = []
    client = _client(seen, runtime, status=503)
    with call_context("default", "run_1"), llm_call_scope("llm_1"):
        assert client.ask("x").fallback_used
    assert [_meta(h)["attempt"] for h in seen] == [1, 2]
    assert {_meta(h)["call_id"] for h in seen} == {"llm_1"}


def test_header_builder_error_is_fail_open(monkeypatch) -> None:
    import app.llm as llm

    monkeypatch.setattr(llm, "current_llm_call_id", lambda: 1 / 0)
    seen: list[dict[str, str]] = []
    with call_context("default", "run_1", "e1"):
        assert _client(seen).ask("x").text == "hi"
    assert "x-litellm-spend-logs-metadata" not in seen[0]


def test_llm_call_scope_propagates_into_copied_context_worker() -> None:
    assert current_llm_call_id() is None
    with llm_call_scope("llm_9"):
        ctx = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(ctx.run, current_llm_call_id).result() == "llm_9"
    assert current_llm_call_id() is None


def test_loop_request_call_ids_equal_capture_call_ids(tmp_path: Path) -> None:
    seen: list[dict[str, str]] = []
    loop = OrchestrationLoop(
        llm_client=_client(seen),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1"),
    )
    sub = loop.prepare_run(QUESTION, eval_run_id="ev-1")
    loop.execute(sub)
    lines = (tmp_path / "t1" / "captures.jsonl").read_text().splitlines()
    captured = [
        r["call_id"]
        for r in map(json.loads, lines)
        if r["record_type"] == "model_call"
    ]
    assert len(captured) == 2  # proposal + answer
    assert [_meta(h)["call_id"] for h in seen] == captured
    assert {_meta(h)["run_id"] for h in seen} == {sub.run_id}
    assert {h["x-litellm-tags"] for h in seen} == {"eval_run_id:ev-1"}

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from app import mcp_client
from app.events import InMemoryEventPublisher
from app.llm import LocalFakeLLMClient
from app.main import create_app
from app.mcp_client import (
    MCP_FAILURE_REASONS,
    FastMCPDatasetProfileClient,
    MCPToolError,
    sanitize_dataset_profile,
    sanitize_dataset_schema,
    sanitize_describe_result,
    sanitize_governed_query_result,
    sanitize_query_result,
)
from app.orchestration import OrchestrationError, OrchestrationLoop
from app.state import InMemoryStateRepository
from fastapi.testclient import TestClient


def _query_result(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "columns": ["zone", "trips"],
        "rows": [["JFK", 1]],
        "row_count": 1,
        "execution_duration_ms": 3,
        "query_id": "q-1",
        "truncated": False,
    }
    payload.update(overrides)
    return payload


def _reason_of(call, *args) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(MCPToolError) as exc_info:
        call(*args)
    return exc_info.value.reason


def test_reason_set_is_closed_and_default_is_unspecified() -> None:
    assert MCP_FAILURE_REASONS == {
        "row_limit_exceeded",
        "byte_limit_exceeded",
        "invalid_result_shape",
        "invalid_cell_value",
        "tool_error_envelope",
        "invalid_request",
        "unknown_tool",
        "transport_error",
        "not_configured",
        "unspecified",
    }
    assert MCPToolError(retryable=False).reason == "unspecified"
    with pytest.raises(ValueError):
        MCPToolError(retryable=False, reason="free text with row data")


def test_every_raise_site_in_mcp_client_names_its_reason() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mcp_client))
    untagged = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "MCPToolError"
        and not any(kw.arg == "reason" for kw in node.keywords)
    ]
    assert untagged == []


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (_query_result(rows=[["a", 1]] * 65, row_count=65), "row_limit_exceeded"),
        (_query_result(rows=[["a" * 129, 1]]), "invalid_cell_value"),
        (_query_result(rows=[[float("inf"), 1]]), "invalid_cell_value"),
        (_query_result(rows=[[{"x": 1}, 1]]), "invalid_cell_value"),
        (_query_result(rows=[["a"]]), "invalid_result_shape"),
        (_query_result(row_count=5), "invalid_result_shape"),
        ({"columns": []}, "invalid_result_shape"),
        (
            _query_result(rows=[["x" * 120, "y" * 120]] * 40, row_count=40),
            "byte_limit_exceeded",
        ),
    ],
)
def test_sanitize_query_result_reasons(payload: dict[str, Any], reason: str) -> None:
    assert _reason_of(sanitize_query_result, payload) == reason


def test_sanitize_governed_query_result_reasons() -> None:
    assert (
        _reason_of(sanitize_governed_query_result, {"columns": []})
        == "invalid_result_shape"
    )
    assert (
        _reason_of(
            sanitize_governed_query_result,
            {**_query_result(), "query_class": ""},
        )
        == "invalid_result_shape"
    )
    assert (
        _reason_of(
            sanitize_governed_query_result,
            {**_query_result(), "dimensions": ["a"] * 17},
        )
        == "invalid_result_shape"
    )
    assert (
        _reason_of(
            sanitize_governed_query_result,
            _query_result(rows=[["a" * 129, 1]]),
        )
        == "invalid_cell_value"
    )


def test_sanitize_schema_profile_and_describe_reasons() -> None:
    assert _reason_of(sanitize_dataset_schema, {}) == "invalid_result_shape"
    assert _reason_of(sanitize_dataset_profile, {}) == "invalid_result_shape"
    assert _reason_of(sanitize_describe_result, {}) == "invalid_result_shape"


class _FakeClient:
    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self._data, self._error = data, error

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def call_tool(self, name: str, arguments: Any = None) -> Any:
        if self._error is not None:
            raise self._error
        return SimpleNamespace(data=self._data)


def _client_returning(monkeypatch, fake: _FakeClient) -> FastMCPDatasetProfileClient:
    client = FastMCPDatasetProfileClient("http://mcp.example/mcp")
    monkeypatch.setattr(client, "_client", lambda: fake)
    return client


def test_client_side_rejections_are_invalid_request() -> None:
    client = FastMCPDatasetProfileClient("http://mcp.example/mcp")
    for call in (
        lambda: client.query_taxi_data(analysis="drop_table", limit=5),
        lambda: client.query_taxi_data(analysis="top_pickup_zones", limit=99),
        lambda: client.average_trip_metrics(region_name=" "),
        lambda: client.describe_taxi_dataset(include_column_stats="yes"),  # type: ignore[arg-type]
    ):
        with pytest.raises(MCPToolError) as exc_info:
            call()
        assert exc_info.value.reason == "invalid_request"


def test_transport_failure_is_transport_error(monkeypatch) -> None:
    client = _client_returning(monkeypatch, _FakeClient(error=OSError("boom")))
    with pytest.raises(MCPToolError) as exc_info:
        client.query_taxi_data(analysis="top_pickup_zones", limit=5)
    assert exc_info.value.reason == "transport_error"
    assert exc_info.value.retryable is True


def test_non_dict_result_is_invalid_result_shape(monkeypatch) -> None:
    client = _client_returning(monkeypatch, _FakeClient(data="nope"))
    with pytest.raises(MCPToolError) as exc_info:
        client.query_taxi_data(analysis="top_pickup_zones", limit=5)
    assert exc_info.value.reason == "invalid_result_shape"


def test_error_envelopes_are_tool_error_envelope(monkeypatch) -> None:
    envelope = {"error": {"retryable": False, "message": "bad"}}
    client = _client_returning(monkeypatch, _FakeClient(data=envelope))
    for call in (
        lambda: client.average_trip_metrics(),
        lambda: client.aggregate_taxi_data(dimensions=[], measures=[]),
    ):
        with pytest.raises(MCPToolError) as exc_info:
            call()
        assert exc_info.value.reason == "tool_error_envelope"


class _FakeMCP:
    def __init__(self, error: MCPToolError | None = None) -> None:
        self._error = error

    def get_dataset_schema(self) -> dict[str, Any]:
        return {
            "dataset": "nyc-taxi",
            "month": "2024-01",
            "columns": ["PULocationID", "trip_distance"],
        }

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        return _query_result()


def _run_failing(error: MCPToolError):  # type: ignore[no-untyped-def]
    repo = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=_FakeMCP(error),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=publisher,
    )
    with pytest.raises(OrchestrationError) as exc_info:
        loop.run("Which pickup zones have the most trips?")
    run_id = next(iter(repo._runs))
    return repo, publisher, run_id, exc_info.value


def _sse_payload(text: str, event_type: str) -> dict[str, Any]:
    for frame in text.strip().split("\n\n"):
        lines = frame.splitlines()
        if f"event: {event_type}" in lines:
            data = next(line for line in lines if line.startswith("data: "))
            return json.loads(data.removeprefix("data: "))["payload"]
    raise AssertionError(f"{event_type} not in stream")


def test_tool_failure_carries_reason_on_event_step_run_and_replay() -> None:
    repo, publisher, run_id, err = _run_failing(
        MCPToolError(
            retryable=False, message="secret row data", reason="row_limit_exceeded"
        )
    )
    assert err.tool_error == {
        "reason": "row_limit_exceeded",
        "tool_name": "query_taxi_data",
    }
    events = publisher.get_events_for_run(run_id)
    tool_failed = next(e for e in events if e.event_type == "tool.failed")
    assert tool_failed.payload["reason"] == "row_limit_exceeded"
    step = next(s for s in repo.list_run_steps(run_id) if s.status == "failed")
    assert step.metadata["reason"] == "row_limit_exceeded"
    live = next(e for e in events if e.event_type == "run.failed").payload
    assert live["tool_error"] == {
        "reason": "row_limit_exceeded",
        "tool_name": "query_taxi_data",
    }
    assert repo.get_run(run_id).metadata["tool_error"] == live["tool_error"]
    replayed = _sse_payload(
        TestClient(create_app(state_repository=repo))
        .get(f"/api/runs/{run_id}/events")
        .text,
        "run.failed",
    )
    assert replayed == live


def test_unspecified_reason_still_surfaces() -> None:
    _, publisher, run_id, _ = _run_failing(MCPToolError(retryable=False))
    live = next(
        e for e in publisher.get_events_for_run(run_id) if e.event_type == "run.failed"
    ).payload
    assert live["tool_error"]["reason"] == "unspecified"


def test_replay_includes_provider_error_from_durable_metadata() -> None:
    from app.state import Conversation, Run

    repo = InMemoryStateRepository()
    repo.create_conversation(Conversation(conversation_id="conv_replay_provider"))
    run = Run(
        run_id="run_replay_provider",
        conversation_id="conv_replay_provider",
        message_id="msg_replay_provider",
        status="failed",
        model="m",
        prompt_version="m9.v1",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        failure_code="llm_provider_error",
        metadata={
            "error": "x",
            "retryable": True,
            "telemetry": {},
            "provider_error": {"status_code": 429},
        },
    )
    repo.create_run(run)
    replayed = _sse_payload(
        TestClient(create_app(state_repository=repo))
        .get("/api/runs/run_replay_provider/events")
        .text,
        "run.failed",
    )
    assert replayed["provider_error"] == {"status_code": 429}
    assert "tool_error" not in replayed


def test_no_mcp_client_configured_raises_not_configured_not_type_error() -> None:
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        state_repository=InMemoryStateRepository(),
    )
    with pytest.raises(OrchestrationError) as exc_info:
        loop.run("Which pickup zones have the most trips?")
    assert exc_info.value.code == "mcp_tool_error"
    assert exc_info.value.retryable is False
    assert exc_info.value.tool_error["reason"] == "not_configured"

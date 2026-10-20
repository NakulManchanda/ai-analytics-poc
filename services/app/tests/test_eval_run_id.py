from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.capture import CaptureSettings
from app.capture.recorder import CaptureRecorder
from app.events.models import RunEvent
from app.events.publisher import InMemoryEventPublisher
from app.llm import LocalFakeLLMClient
from app.main import create_app
from app.model_policy import current_call_context
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository
from fastapi.testclient import TestClient
from test_orchestration_loop import FakeMCPClient

PROMPT = "What are the top pickup zones by ride count?"


class SpyLoop:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def prepare_run(self, prompt: str, conversation_id: str | None = None, **kw: Any):
        self.calls.append(kw)
        return SimpleNamespace(
            prompt=prompt, conversation_id="c", message_id="m", run_id="r"
        )

    def execute(self, submission: Any) -> None:
        return None


def _client() -> tuple[TestClient, SpyLoop]:
    loop = SpyLoop()
    app = create_app(orchestration_loop=loop, run_dispatcher=lambda t: None)  # type: ignore[arg-type]
    return TestClient(app), loop


@pytest.mark.parametrize("bad", ["", "has space", "a/b", "x" * 65, "é"])
def test_malformed_body_id_rejected(bad: str) -> None:
    client, loop = _client()
    r = client.post("/api/runs", json={"prompt": "hi", "eval_run_id": bad})
    assert r.status_code == 422
    assert loop.calls == []


@pytest.mark.parametrize("bad", ["", "has space", "x" * 65])
def test_malformed_header_rejected(bad: str) -> None:
    client, loop = _client()
    r = client.post("/api/runs", json={"prompt": "hi"}, headers={"x-eval-run-id": bad})
    assert r.status_code == 422
    assert loop.calls == []


def test_absent_passes_nothing() -> None:
    client, loop = _client()
    assert client.post("/api/runs", json={"prompt": "hi"}).status_code == 202
    assert loop.calls == [{}]


def test_header_fallback_and_body_precedence() -> None:
    client, loop = _client()
    client.post("/api/runs", json={"prompt": "hi"}, headers={"x-eval-run-id": "H.1:a"})
    client.post(
        "/api/runs",
        json={"prompt": "hi", "eval_run_id": "body-1"},
        headers={"x-eval-run-id": "H.1:a"},
    )
    assert loop.calls == [{"eval_run_id": "H.1:a"}, {"eval_run_id": "body-1"}]


def test_event_serialization_only_when_set() -> None:
    plain = RunEvent("run.received", "r", "c", 1)
    tagged = RunEvent("run.received", "r", "c", 1, eval_run_id="e1")
    assert "eval_run_id" not in plain.to_dict()
    assert "eval_run_id" not in plain.to_sse()
    assert tagged.to_dict()["eval_run_id"] == "e1"
    assert '"eval_run_id": "e1"' in tagged.to_sse()
    assert RunEvent.from_dict(tagged.to_dict()).eval_run_id == "e1"
    assert RunEvent.from_dict(plain.to_dict()).eval_run_id is None


def _drive(tmp_path: Path, eval_run_id: str | None):
    repo = InMemoryStateRepository()
    pub = InMemoryEventPublisher()
    seen: list[Any] = []
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        event_publisher=pub,
        capture=CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1"),
    )
    original = loop._execute_loop

    def spy(**kw: Any):
        seen.append(current_call_context().eval_run_id)
        return original(**kw)

    loop._execute_loop = spy  # type: ignore[method-assign]
    sub = (
        loop.prepare_run(PROMPT, eval_run_id=eval_run_id)
        if eval_run_id
        else loop.prepare_run(PROMPT)
    )
    loop.execute(sub)
    run = repo.get_run(sub.run_id)
    events = pub.get_events_for_run(sub.run_id)
    records = [
        json.loads(line)
        for line in (tmp_path / "t1" / "captures.jsonl").read_text().splitlines()
    ]
    return run, events, records, seen


def test_present_reaches_metadata_events_capture_and_context(tmp_path: Path) -> None:
    run, events, records, seen = _drive(tmp_path, "base-1")
    assert run is not None and run.metadata["eval_run_id"] == "base-1"
    assert len(events) > 2 and all(e.eval_run_id == "base-1" for e in events)
    assert {r["record_type"] for r in records} == {"model_call", "tool_call"}
    assert all(r["eval_run_id"] == "base-1" for r in records)
    assert seen == ["base-1"]


def test_absent_leaves_no_key_anywhere(tmp_path: Path) -> None:
    run, events, records, seen = _drive(tmp_path, None)
    assert run is not None and "eval_run_id" not in run.metadata
    assert all("eval_run_id" not in e.to_dict() for e in events)
    assert all("eval_run_id" not in r for r in records)
    assert seen == [None]

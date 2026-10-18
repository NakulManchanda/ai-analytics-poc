from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.capture import (
    SCHEMA_VERSION,
    CaptureSettings,
    JsonlCaptureSink,
    NullCaptureSink,
    export_jsonl,
    purge,
)
from app.capture.recorder import CaptureRecorder
from app.llm import LocalFakeLLMClient
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository
from test_orchestration_loop import FakeMCPClient


def _model(call_id: str, run_id: str = "run_1", step: int = 1, **kw: Any) -> dict:
    base = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "model_call",
        "run_id": run_id,
        "call_id": call_id,
        "step": step,
    }
    base.update(kw)
    return base


def _run_loop(tmp_path: Path, content: bool = False, tenant: str = "t1"):
    repo = InMemoryStateRepository()
    recorder = CaptureRecorder(
        CaptureSettings(directory=tmp_path, include_content=content),
        tenant_id=tenant,
    )
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        capture=recorder,
    )
    result = loop.run("What are the top pickup zones by ride count?")
    return result, recorder


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_sink_idempotent_on_retry(tmp_path: Path) -> None:
    sink = JsonlCaptureSink(tmp_path, "t1")
    assert sink.write(_model("c1")) is True
    assert sink.write(_model("c1")) is False
    # a fresh sink instance (process restart) also sees the existing key
    assert JsonlCaptureSink(tmp_path, "t1").write(_model("c1")) is False
    assert len(_read(tmp_path / "t1" / "captures.jsonl")) == 1


def test_sink_failure_is_counted_not_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    sink = JsonlCaptureSink(blocker, "t1")  # cannot mkdir under a file
    assert sink.write(_model("c1")) is False
    assert sink.dropped == 1


def test_tenant_isolation_and_path_traversal(tmp_path: Path) -> None:
    JsonlCaptureSink(tmp_path, "a").write(_model("c1"))
    JsonlCaptureSink(tmp_path, "b").write(_model("c2"))
    assert len(_read(tmp_path / "a" / "captures.jsonl")) == 1
    assert len(_read(tmp_path / "b" / "captures.jsonl")) == 1
    with pytest.raises(ValueError):
        JsonlCaptureSink(tmp_path, "../evil")


def test_export_sorted_deduped(tmp_path: Path) -> None:
    sink = JsonlCaptureSink(tmp_path, "t1")
    sink.write(_model("c2", run_id="run_b", step=2))
    sink.write(_model("c1", run_id="run_b", step=1))
    sink.write(_model("c3", run_id="run_a", step=3))
    out = tmp_path / "out.jsonl"
    assert export_jsonl(tmp_path, "t1", out) == 3
    assert [(r["run_id"], r["step"]) for r in _read(out)] == [
        ("run_a", 3),
        ("run_b", 1),
        ("run_b", 2),
    ]
    assert export_jsonl(tmp_path, "t1", out) == 3  # re-export is stable


def test_purge(tmp_path: Path) -> None:
    sink = JsonlCaptureSink(tmp_path, "t1")
    sink.write(_model("c1", run_id="run_a"))
    sink.write(_model("c2", run_id="run_b"))
    assert purge(tmp_path, "t1", run_id="run_a") == 1
    assert [r["run_id"] for r in _read(tmp_path / "t1" / "captures.jsonl")] == ["run_b"]
    purge(tmp_path, "t1")
    assert not (tmp_path / "t1").exists()


def test_run_timeline_metadata_only(tmp_path: Path) -> None:
    result, recorder = _run_loop(tmp_path)
    records = _read(tmp_path / "t1" / "captures.jsonl")
    assert recorder.sink.dropped == 0
    assert {r["run_id"] for r in records} == {result.run_id}
    assert all(r["schema_version"] == "capture.v1" for r in records)
    assert all(r["tenant_id"] == "t1" for r in records)
    kinds = [r["record_type"] for r in sorted(records, key=lambda r: r["step"])]
    assert kinds == ["model_call", "tool_call", "model_call"]
    roles = [r["role"] for r in records if r["record_type"] == "model_call"]
    assert sorted(roles) == ["answer", "planner"]
    tool = next(r for r in records if r["record_type"] == "tool_call")
    assert tool["tool_call_id"] == result.tool_call_id
    assert tool["query_id"] == "fake-query-123"
    assert tool["row_count"] == 2
    assert "rows" not in json.dumps(tool) and "JFK" not in json.dumps(tool)
    # metadata only: no raw content, but a hash is present
    for r in records:
        assert r.get("question") is None and r.get("answer") is None
    answer_rec = next(r for r in records if r.get("role") == "answer")
    assert answer_rec["answer_sha256"] and answer_rec["question_sha256"]
    assert "JFK" not in (tmp_path / "t1" / "captures.jsonl").read_text()


def test_opt_in_content(tmp_path: Path) -> None:
    result, _ = _run_loop(tmp_path, content=True)
    records = _read(tmp_path / "t1" / "captures.jsonl")
    answer_rec = next(r for r in records if r.get("role") == "answer")
    assert "JFK" in answer_rec["answer"]
    assert "top pickup zones" in answer_rec["question"]


def test_content_size_cap(tmp_path: Path) -> None:
    recorder = CaptureRecorder(
        CaptureSettings(directory=tmp_path, include_content=True, max_field_bytes=10),
        tenant_id="t1",
    )
    assert len(recorder.cap("x" * 100).encode()) <= 10 + len("…[truncated]".encode())


def test_unknown_tokens_are_null_not_zero(tmp_path: Path) -> None:
    from app.capture.recorder import _tokens

    assert _tokens(0) is None
    assert _tokens(None) is None
    assert _tokens(12) == 12


def test_capture_failure_never_blocks_response(tmp_path: Path) -> None:
    class Boom(NullCaptureSink):
        def write(self, record: dict) -> bool:
            raise RuntimeError("disk full")

    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1")
    recorder.sink = Boom()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=recorder,
    )
    result = loop.run("What are the top pickup zones by ride count?")
    assert result.status == "completed"
    assert recorder.dropped >= 1


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURE_DIR", raising=False)
    assert CaptureSettings.from_environment().directory is None
    assert CaptureRecorder.from_environment() is None


def test_repeat_capture_of_same_run_is_idempotent(tmp_path: Path) -> None:
    repo = InMemoryStateRepository()
    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path), tenant_id="t1")
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=repo,
        capture=recorder,
    )
    result = loop.run("What are the top pickup zones by ride count?")
    n = len(_read(tmp_path / "t1" / "captures.jsonl"))
    recorder.capture_run(repo, result.run_id, "q", result.answer)
    assert len(_read(tmp_path / "t1" / "captures.jsonl")) == n

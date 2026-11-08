"""Phoenix trace-continuity check (#226); span shape is what Phoenix's REST API returns."""

from __future__ import annotations

import json
from typing import Any

import pytest

from evals.cli import main
from evals.schema import TraceRecord
from evals.trace_check import TraceCheckError, check_traces, phoenix_fetch

ROOT_ATTRS = {
    "ai.run_id": "run_1",
    "ai.status": "completed",
    "agent.strategy": "deep_agents",
    "agent.framework": "deepagents",
    "agent.step_count": 2,
}


def _span(
    name: str, sid: str, parent: str | None, attrs: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "context": {"trace_id": "t1", "span_id": sid},
        "parent_id": parent,
        "attributes": attrs or {},
    }


def _good() -> list[dict[str, Any]]:
    return [
        _span("ai.run", "a", None, dict(ROOT_ATTRS)),
        _span("litellm_proxy_request", "b", "a"),
        _span("litellm_request", "c", "b"),
        _span("mcp.request", "d", "a"),
        _span("mcp.tool.execute", "e", "d"),
        _span("duckdb.query", "f", "e"),
    ]


def _rec(
    kind: str, call_id: str, trace_id: str | None = "t1", status: str | None = None
) -> TraceRecord:
    return TraceRecord(
        status=status or ("completed" if kind == "tool_call" else None),
        record_type=kind,  # type: ignore[arg-type]
        run_id="run_1",
        turn_id="t",
        step=1,
        call_id=call_id,
        trace_id=trace_id,
    )


RECORDS = [_rec("model_call", "m1"), _rec("tool_call", "k1")]


def _problems(spans: list[dict[str, Any]], records=RECORDS) -> list[str]:
    return check_traces(records, lambda _t: spans).problems


def test_complete_continuous_trace_passes() -> None:
    result = check_traces(RECORDS, lambda _t: _good())
    assert result.ok and result.runs[0].strategy == "deep_agents"
    assert result.to_json()["ok"] is True


def test_empty_trace_fails() -> None:
    assert any("no spans" in p for p in _problems([]))


def test_capture_without_trace_id_fails_without_querying() -> None:
    def boom(_t: str) -> list[dict[str, Any]]:
        raise AssertionError("must not query")

    result = check_traces([_rec("model_call", "m1", None)], boom)
    assert any("no trace_id" in p for p in result.problems)


def test_detached_litellm_span_is_broken_parentage() -> None:
    spans = _good()
    spans[1]["parent_id"] = "missing-parent"  # litellm span orphaned from ai.run
    assert any("not under ai.run" in p for p in _problems(spans))


def test_ai_run_with_missing_parent_fails() -> None:
    spans = _good()
    spans[0]["parent_id"] = "not-in-trace"
    problems = _problems(spans)
    assert any("must be the trace root" in p for p in problems)
    assert any("not under ai.run" in p for p in problems)


def test_ai_run_with_a_parent_in_the_trace_fails() -> None:
    spans = _good()
    spans[0][
        "parent_id"
    ] = "d"  # ai.run under an mcp span, which itself is under ai.run
    problems = _problems(spans)
    assert any("must be the trace root" in p for p in problems)
    assert any("not under ai.run" in p for p in problems)


def test_parent_cycle_fails() -> None:
    spans = _good()
    spans[1]["parent_id"] = "c"  # b -> c -> b, cut off from ai.run
    assert any("not under ai.run" in p for p in _problems(spans))


def test_self_parent_fails() -> None:
    spans = _good()
    spans[3]["parent_id"] = "d"
    assert any("not under ai.run" in p for p in _problems(spans))


def test_conflicting_trace_ids_in_one_run_fail_without_querying() -> None:
    def boom(_t: str) -> list[dict[str, Any]]:
        raise AssertionError("must not query")

    records = [_rec("model_call", "m1", "t1"), _rec("model_call", "m2", "t2")]
    result = check_traces(records, boom)
    assert any("conflicting trace_ids: t1, t2" in p for p in result.problems)


def test_missing_root_attributes_and_wrong_run_id() -> None:
    spans = _good()
    del spans[0]["attributes"]["agent.strategy"]
    spans[0]["attributes"]["ai.run_id"] = "other"
    problems = _problems(spans)
    assert any("lacks attributes: agent.strategy" in p for p in problems)
    assert any("!= capture run_id" in p for p in problems)


def test_two_roots_fail() -> None:
    assert any(
        "exactly one ai.run" in p
        for p in _problems(_good() + [_span("ai.run", "z", None, dict(ROOT_ATTRS))])
    )


def test_missing_litellm_or_mcp_spans_fail_against_capture_counts() -> None:
    spans = [
        s
        for s in _good()
        if s["name"] not in ("litellm_proxy_request", "mcp.tool.execute")
    ]
    problems = _problems(spans)
    assert any(
        "0 litellm_proxy_request spans for 1 capture model calls" in p for p in problems
    )
    assert any(
        "0 mcp.tool.execute spans for 1 completed capture tool calls" in p
        for p in problems
    )


def test_failed_tool_call_needs_no_execute_span_but_completed_does() -> None:
    no_exec = [
        s for s in _good() if s["name"] not in ("mcp.tool.execute", "duckdb.query")
    ]
    failed = [_rec("model_call", "m1"), _rec("tool_call", "k1", status="failed")]
    assert check_traces(failed, lambda _t: no_exec).ok
    completed = [_rec("model_call", "m1"), _rec("tool_call", "k1")]
    assert not check_traces(completed, lambda _t: no_exec).ok


def test_no_runs_is_not_ok() -> None:
    result = check_traces([], lambda _t: _good())
    assert not result.ok and result.to_json()["problems"] == ["no runs in capture"]


def test_unreachable_phoenix_raises_instead_of_passing() -> None:
    fetch = phoenix_fetch("http://127.0.0.1:1", timeout=1)
    with pytest.raises(TraceCheckError):
        fetch("t1")


def _write_run(tmp_path, trace_id: str | None = "t1"):
    run = "lab-20260101T000000Z"
    run_dir = tmp_path / run
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"status": "complete"}))
    lines = [
        json.dumps(
            {
                "schema_version": "capture.v1",
                "record_type": "model_call",
                "run_id": "run_1",
                "turn_id": "t",
                "step": 1,
                "call_id": "m1",
                "trace_id": trace_id,
            }
        )
    ]
    (run_dir / "captures.snapshot.jsonl").write_text("\n".join(lines) + "\n")
    return run, run_dir


def test_cli_unreachable_phoenix_exits_2(tmp_path, capsys) -> None:
    run, _ = _write_run(tmp_path)
    code = main(
        [
            "run-trace-check",
            "--root",
            str(tmp_path),
            "--run",
            run,
            "--phoenix-url",
            "http://127.0.0.1:1",
        ]
    )
    assert code == 2 and "Phoenix query failed" in capsys.readouterr().err


def test_cli_problems_exit_4_and_write_report(tmp_path, capsys, monkeypatch) -> None:
    run, run_dir = _write_run(tmp_path)
    monkeypatch.setattr(
        "evals.trace_check.phoenix_fetch", lambda *_a, **_k: lambda _t: []
    )
    code = main(["run-trace-check", "--root", str(tmp_path), "--run", run])
    assert code == 4 and "PROBLEM:" in capsys.readouterr().err
    report = json.loads((run_dir / "report" / "trace_check.json").read_text())
    assert report["ok"] is False

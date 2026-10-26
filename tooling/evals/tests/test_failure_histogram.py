"""Failure-code histogram in run-compare / run-review (synthetic events)."""

import json

from test_run_compare import setup_run

from evals.cli import main
from evals.runs import (
    declined_histogram,
    event_declined_outcome,
    event_failure_code,
    failure_histogram,
    format_histogram,
)


def _frame(payload: dict, wrapped: bool = True) -> str:
    body = {"payload": payload} if wrapped else payload
    return f"event: run.started\ndata: {{}}\n\nevent: run.failed\ndata: {json.dumps(body)}\n\n"


def test_event_failure_code_variants(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text(_frame({"failure_code": "malformed_tool_use"}))
    assert event_failure_code(f) == "malformed_tool_use"
    f.write_text(_frame({"failure_code": "no_tool_call"}, wrapped=False))
    assert event_failure_code(f) == "no_tool_call"
    f.write_text(_frame({"failure_code": None}))
    assert event_failure_code(f) == "unknown"
    f.write_text("event: run.started\ndata: {}\n\n")
    assert event_failure_code(f) == "unknown"
    f.write_text("event: run.failed\ndata: not-json\n\n")
    assert event_failure_code(f) == "unknown"
    assert event_failure_code(tmp_path / "missing.txt") == "unknown"


def test_histogram_counts_only_failed_rows(tmp_path):
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "r1.txt").write_text(_frame({"failure_code": "x"}))
    (tmp_path / "events" / "r2.txt").write_text(_frame({"failure_code": "x"}))
    rows = [
        {"terminal": "run.failed", "run_id": "r1"},
        {"terminal": "run.failed", "run_id": "r2"},
        {"terminal": "run.failed", "run_id": "r9"},  # no events file
        {"terminal": "run.completed", "run_id": "r3"},
    ]
    hist = failure_histogram(tmp_path, rows)
    assert dict(hist) == {"x": 2, "unknown": 1}
    assert format_histogram(hist) == "unknown: 1, x: 2"


def test_compare_and_review_show_histogram(tmp_path):
    run, base = setup_run(tmp_path)
    (run / "events").mkdir()
    (run / "events" / "r3.txt").write_text(
        _frame({"failure_code": "malformed_tool_use"})
    )
    assert (
        main(["run-compare", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    assert (
        "## Failure codes (this run)\n\nmalformed_tool_use: 1"
        in (run / "compare.md").read_text()
    )
    assert (
        main(["run-review", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    assert "Failure codes: malformed_tool_use: 1" in (run / "review.html").read_text()


def _declined(outcome, reason="r", wrapped=True) -> str:
    body = {"outcome": outcome, "reason": reason, "tool_source": None}
    body = {"payload": body} if wrapped else body
    return (
        "event: run.started\ndata: {}\n\n"
        f"event: run.declined\ndata: {json.dumps(body)}\n\n"
        "event: run.completed\ndata: {}\n\n"
    )


def test_event_declined_outcome_variants(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text(_declined("no_visible_output"))
    assert event_declined_outcome(f) == "no_visible_output"
    f.write_text(_declined("unsupported", wrapped=False))
    assert event_declined_outcome(f) == "unsupported"
    f.write_text(_declined(None))
    assert event_declined_outcome(f) == "unknown"
    f.write_text("event: run.declined\ndata: not-json\n\n")
    assert event_declined_outcome(f) is None
    f.write_text("event: run.started\ndata: {}\n\n")
    assert event_declined_outcome(f) is None
    assert event_declined_outcome(tmp_path / "missing.txt") is None


def test_declined_histogram_counts_frames_only(tmp_path):
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "r1.txt").write_text(_declined("no_visible_output"))
    (tmp_path / "events" / "r2.txt").write_text(_declined("unsupported"))
    (tmp_path / "events" / "r3.txt").write_text(_declined("no_visible_output"))
    (tmp_path / "events" / "r4.txt").write_text(
        "event: run.started\ndata: {}\n\nevent: run.completed\ndata: {}\n\n"
    )
    rows = [{"terminal": "run.completed", "run_id": f"r{i}"} for i in range(1, 5)]
    hist = declined_histogram(tmp_path, rows)
    assert dict(hist) == {"no_visible_output": 2, "unsupported": 1}
    assert format_histogram(hist) == "no_visible_output: 2, unsupported: 1"
    assert format_histogram(declined_histogram(tmp_path, [rows[3]])) == "none"


def test_compare_and_review_show_declined_histogram(tmp_path):
    run, base = setup_run(tmp_path)
    (run / "events").mkdir()
    (run / "events" / "r3.txt").write_text(_declined("no_visible_output"))
    assert (
        main(["run-compare", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    assert (
        "## Declined outcomes (this run)\n\nno_visible_output: 1"
        in (run / "compare.md").read_text()
    )
    assert (
        main(["run-review", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    assert (
        "Declined outcomes: no_visible_output: 1" in (run / "review.html").read_text()
    )

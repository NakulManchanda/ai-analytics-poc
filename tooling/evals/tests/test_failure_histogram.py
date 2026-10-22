"""Failure-code histogram in run-compare / run-review (synthetic events)."""

import json

from test_run_compare import setup_run

from evals.cli import main
from evals.runs import event_failure_code, failure_histogram, format_histogram


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

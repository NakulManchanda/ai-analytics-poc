"""Report-table parser, compare classification, and review.html."""

import json

from evals.cli import main
from evals.runs import direction, load_run_reports

ROWS = [
    {
        "turn": 0,
        "case_id": "det-001",
        "turn_index": 0,
        "graded": True,
        "question": "Q one <script>alert(1)</script>",
        "execution": "correct",
        "tool": "correct",
        "answer": "correct",
        "reasons": "",
        "actual": 'aggregate_taxi_data({"a": 1}) completed rows=1',
        "expected": ["aggregate_taxi_data"],
        "answer_text": "First line.\n\nSecond <b>x</b>.",
        "cost_usd": 0.0001,
        "model_calls": 2,
    },
    {
        "turn": 1,
        "case_id": "det-006",
        "turn_index": 1,
        "graded": True,
        "question": "Q two",
        "execution": "correct",
        "tool": "incorrect",
        "answer": "incorrect",
        "reasons": "tool: not matched",
        "actual": "none",
        "expected": ["query_taxi_data"],
        "answer_text": "Short.",
        "cost_usd": 0.00015,
        "model_calls": 1,
    },
]


def test_load_reports_and_cost(tmp_path):
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "c.json").write_text(json.dumps({"rows": ROWS}))
    turns, cost = load_run_reports(tmp_path)
    assert turns[("det-006", 1)]["expected"] == "query_taxi_data"
    assert turns[("det-001", 0)]["answer_text"].startswith("First line.")
    assert abs(cost - 0.00025) < 1e-12


def test_direction_four_states():
    assert direction("incorrect", "correct") == "improved"
    assert direction("correct", "incorrect") == "regressed"
    assert direction("correct", "correct") == "unchanged"
    assert direction("needs_review", "not_applicable") == "lateral"
    for old, new in (
        ("-", "correct"),
        ("correct", "not_graded"),
        ("missing", "correct"),
    ):
        assert direction(old, new) == "missing_evidence"


def setup_run(tmp_path):
    run = tmp_path / "r-20200101T000000Z"
    (run / "report").mkdir(parents=True)
    (run / "report" / "conv_a.json").write_text(json.dumps({"rows": ROWS}))
    rows = [
        {
            "case_id": "det-001",
            "turn_index": 0,
            "question": "Q one <script>alert(1)</script>",
            "terminal": "run.completed",
            "run_id": "r1",
        },
        {
            "case_id": "det-006",
            "turn_index": 1,
            "question": "Q two",
            "terminal": "run.completed",
            "run_id": "r2",
        },
        {
            "case_id": "uns-001",
            "turn_index": 0,
            "question": "Q three",
            "terminal": "run.failed",
            "run_id": "r3",
        },
    ]
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    base = tmp_path / "base.jsonl"
    b = [
        ("det-001", 0, "correct", "incorrect", "incorrect"),
        ("det-006", 1, "correct", "correct", "correct"),
        ("uns-001", 0, "correct", "correct", "correct"),
    ]
    base.write_text(
        "".join(
            json.dumps(
                dict(case_id=c, turn_index=t, execution_v2=e, tool_v2=to, answer_v2=a)
            )
            + "\n"
            for c, t, e, to, a in b
        )
    )
    (run / "state.json").write_text(json.dumps({"status": "complete"}))
    (tmp_path / ".current").write_text(run.name)
    return run, base


def test_compare(tmp_path, capsys):
    run, base = setup_run(tmp_path)
    assert (
        main(["run-compare", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    text = (run / "compare.md").read_text()
    assert (
        "improved: 2, regressed: 3, lateral: 0, unchanged: 2, missing_evidence: 2"
        in text
    )
    assert "| det-006#1 | tool | correct | incorrect | regressed |" in text
    assert "| det-001#0 | tool | incorrect | correct | improved |" in text
    assert "| uns-001#0 | execution | correct | failed | regressed |" in text
    assert "- uns-001#0 r3 run.failed" in text
    assert "$0.000250" in text
    assert "missing_evidence" in text
    assert "## Focus cases" in text and "uns-001#0" in text


def test_review_escapes_and_is_offline(tmp_path, capsys):
    run, base = setup_run(tmp_path)
    assert (
        main(["run-review", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    html = (run / "review.html").read_text()
    assert html.startswith("<!doctype html>")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "http://" not in html and "https://" not in html
    assert "see app/LiteLLM logs" in html and "run.failed" in html
    assert 'data-dirs="missing_evidence regressed"' in html

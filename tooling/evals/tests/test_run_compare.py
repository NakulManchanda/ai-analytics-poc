"""Report-table parser, compare classification, and review.html."""

import json

from evals.cli import main
from evals.runs import parse_report

REPORT = """# Captured session report

## Turns

| # | question | linked golden turn | execution | tool | answer | legacy grading | tools (actual) | tools expected | reasons |
|---|---|---|---|---|---|---|---|---|---|
| 0 | Q one <script>alert(1)</script> | `det-001#0` | correct | correct | correct | pass | aggregate_taxi_data({"a": 1}) completed rows=1 | aggregate_taxi_data | |
| 1 | Q two | `det-006#1` | correct | incorrect | incorrect | FAIL | none | query_taxi_data | tool: not matched |

- turn 0 answer: First line.

Second paragraph <b>x</b>.
- turn 1 answer: Short.

## Tokens, cost, latency

| # | model calls | aliases | input | output | cost USD | model ms | tool ms |
|---|---|---|---|---|---|---|---|
| 0 | 2 | a | 10 | 5 | 0.000100 | 1 | 1 |
| total | | | 20 | 10 | 0.000250 | 2 | 2 |
"""


def test_parse_report():
    p = parse_report(REPORT)
    t = p["turns"][("det-006", 1)]
    assert (t["execution"], t["tool"], t["answer"]) == (
        "correct",
        "incorrect",
        "incorrect",
    )
    assert t["expected"] == "query_taxi_data" and t["answer_text"] == "Short."
    assert "Second paragraph" in p["turns"][("det-001", 0)]["answer_text"]
    assert p["cost"] == 0.00025  # 6th cell of the totals row


def setup_run(tmp_path):
    run = tmp_path / "r-20200101T000000Z"
    (run / "report").mkdir(parents=True)
    (run / "report" / "conv_a.md").write_text(REPORT)
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
    (tmp_path / ".current").write_text(run.name)
    return run, base


def test_compare(tmp_path, capsys):
    run, base = setup_run(tmp_path)
    assert (
        main(["run-compare", "--root", str(tmp_path), "--baseline-results", str(base)])
        == 0
    )
    text = (run / "compare.md").read_text()
    assert "improved: 2, regressed: 5" in text
    assert "| det-006#1 | tool | correct | incorrect | regressed |" in text
    assert "| det-001#0 | tool | incorrect | correct | improved |" in text
    assert "| uns-001#0 | execution | correct | failed | regressed |" in text
    assert "- uns-001#0 r3 run.failed" in text
    assert "$0.000250" in text
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
    assert 'data-reg="1"' in html

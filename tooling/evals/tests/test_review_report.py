"""Human-review report and its section in run-compare (#190)."""

import json

from evals.cli import main
from evals.review_report import queue, render_review_report, summary_lines
from evals.reviews import append_review, new_record
from evals.run_compare import render_compare


def add(run, case="c1", turn=0, dim="tool", verdict="correct", **kw):
    append_review(run, new_record(run, case, turn, dim, verdict, "me", **kw))


def baseline(tmp_path):
    p = tmp_path / "baseline.jsonl"
    p.write_text(
        json.dumps(
            {
                "case_id": "c1",
                "turn_index": 0,
                "execution_v2": "correct",
                "tool_v2": "correct",
                "answer_v2": "correct",
            }
        )
        + "\n"
    )
    return p


def test_no_reviews_means_no_section_and_an_honest_report(run, tmp_path):
    assert summary_lines(run) == []
    assert "## Human review" not in render_compare(run, baseline(tmp_path))
    text = render_review_report(run)
    assert "No reviews recorded yet" in text and "Still to review (2 turns" in text


def test_report_separates_human_from_grader_and_lists_disagreements(run):
    add(
        run,
        dim="tool",
        verdict="incorrect",
        failure_layer="planner",
        notes="wrong | tool",
    )
    add(run, dim="answer", verdict="incorrect")  # grader also says incorrect: agree
    add(
        run, turn=1, dim="tool", verdict="correct"
    )  # follow-up turn: answer + resolution missing
    text = "\n".join(summary_lines(run))
    assert "annotated 1 (partial 1, untouched 0); adjudicated 1" in text
    assert "| tool | 1 | 1 | 0 | 0 |" in text
    assert "- tool: 0 agree, 1 differ" in text and "- answer: 1 agree, 0 differ" in text
    assert "| c1#0 | tool | correct | incorrect | planner | wrong \\| tool |" in text


def test_compare_includes_the_section_once_reviews_exist(run, tmp_path):
    add(run, dim="tool", verdict="incorrect")
    out = render_compare(run, baseline(tmp_path))
    assert out.count("## Human review (separate from grader outcomes)") == 1
    assert "| tool | correct | 1 | 1 |" in out  # grader outcome table untouched


def test_queue_orders_by_information_value(run):
    rows = [json.loads(x) for x in (run / "results.jsonl").read_text().splitlines()]
    rows.append(
        {"case_id": "c2", "turn_index": 0, "question": "q", "terminal": "run.failed"}
    )
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    why = dict(queue(run))
    order = [t for t, _ in queue(run)]
    assert order[0] == "c2#0" and "did not complete" in why["c2#0"]
    assert order.index("c1#0") < order.index(
        "c1#1"
    )  # grader incorrect before unreported/spot check
    add(run, case="c2", dim="tool")
    add(run, case="c2", dim="answer")
    assert "c2#0" not in dict(queue(run))  # turn 0 needs tool + answer only


def test_cli_writes_the_report_file(run, capsys):
    add(run, dim="tool")
    assert (
        main(
            [
                "run-review-report",
                "--root",
                str(run.parent),
                "--run",
                run.name,
                "--allow-incomplete",
            ]
        )
        == 0
    )
    assert (run / "review-report.md").read_text().startswith("# Human review report: ")
    assert "Human review (separate" in capsys.readouterr().out


def test_optional_resolution_on_turn_zero_never_makes_unreviewed_negative(run):
    add(run, dim="resolution")  # turn 0: not a required dimension
    text = "\n".join(summary_lines(run))
    row = next(x for x in text.splitlines() if x.startswith("| resolution |"))
    assert (
        row == "| resolution | 1 | 0 | 0 | 1 (+1 optional) |"
    )  # 1 follow-up turn still open
    assert "-" not in row.split("|")[-2]


def test_human_needs_review_stays_pending_until_resolved(run):
    add(run, dim="tool", verdict="correct")
    add(run, dim="answer", verdict="needs_review", notes="unsure")
    text = "\n".join(summary_lines(run))
    assert "annotated 1 (partial 0, untouched 1); adjudicated 0" in text
    assert "unresolved needs_review 1" in text
    report = render_review_report(run)
    assert "## Unresolved human needs_review (1 turns)" in report and "- c1#0" in report
    add(
        run, dim="answer", verdict="correct", notes="checked reference"
    )  # later verdict wins
    after = "\n".join(summary_lines(run))
    assert "adjudicated 1" in after and "unresolved needs_review 0" in after


def test_report_header_has_digests_and_timestamp(run):
    from datetime import UTC, datetime

    from evals.reviews import reviews_digest, snapshot_digest

    add(run)
    first = render_review_report(run, now=datetime(2026, 10, 31, 6, 0, tzinfo=UTC))
    assert "generated 2026-10-31T06:00:00Z" in first
    assert snapshot_digest(run)[:12] in first and reviews_digest(run)[:12] in first
    add(run, dim="answer")
    second = render_review_report(run, now=datetime(2026, 10, 31, 6, 5, tzinfo=UTC))
    assert reviews_digest(run)[:12] in second and reviews_digest(run)[:12] not in first

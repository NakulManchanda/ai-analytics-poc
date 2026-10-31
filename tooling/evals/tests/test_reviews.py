"""Human review data layer (#190): resolution, digest binding, malformed rows, round trip."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from evals.cli import main
from evals.reviews import (
    append_review,
    disagreements,
    load_human_verdicts,
    load_reviews,
    materialize,
    new_record,
)
from evals.runs import RunError

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "r-20261030T120000Z"
    (d / "report").mkdir(parents=True)
    rows = [{"case_id": "c1", "turn_index": 0}, {"case_id": "c1", "turn_index": 1}]
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    snap = d / "captures.snapshot.jsonl"
    snap.write_text('{"a": 1}\n')
    digest = hashlib.sha256(snap.read_bytes()).hexdigest()
    (d / "captures.snapshot.sha256").write_text(f"{digest}  {snap.name}\n")
    graded = [
        dict(case_id="c1", turn_index=0, graded=True, tool="correct", answer="incorrect",
             actual="x", expected=[], cost_usd=0.0)
    ]  # fmt: skip
    (d / "report" / "conv.json").write_text(json.dumps({"rows": graded}))
    return d


def add(run, verdict="correct", dim="tool", turn=0, minutes=0, **kw):
    rec = new_record(
        run, "c1", turn, dim, verdict, "me", now=T0 + timedelta(minutes=minutes), **kw
    )
    append_review(run, rec)
    return rec


def test_latest_wins_and_supersession_is_auditable(run):
    first = add(run, "incorrect")
    second = add(run, "correct", minutes=5)
    got = load_reviews(run).resolved[("c1", 0, "tool")]
    assert got["review_id"] == second["review_id"] and got["superseded"] == [
        first["review_id"]
    ]


def test_order_is_by_timestamp_not_file_position(run):
    late = add(run, "correct", minutes=9)
    add(run, "incorrect", minutes=1)  # appended later, reviewed earlier
    assert (
        load_reviews(run).resolved[("c1", 0, "tool")]["review_id"] == late["review_id"]
    )


def test_timestamp_tie_falls_back_to_file_order(run):
    add(run, "incorrect")
    last = add(run, "needs_review")
    assert (
        load_reviews(run).resolved[("c1", 0, "tool")]["review_id"] == last["review_id"]
    )


def test_duplicate_review_id_ignored(run):
    rec = add(run)
    with (run / "reviews.jsonl").open("a") as f:
        f.write(json.dumps(dict(rec, verdict="incorrect")) + "\n")
    r = load_reviews(run)
    assert r.duplicates == [rec["review_id"]]
    assert r.resolved[("c1", 0, "tool")]["verdict"] == "correct"


def test_malformed_lines_reported_not_fatal(run):
    ok = add(run)
    bad = [
        "{not json",
        "[1]",
        json.dumps(dict(ok, review_id="a", verdict="maybe")),
        json.dumps(dict(ok, review_id="b", dimension="execution")),
        json.dumps(dict(ok, review_id="c", reviewed_at="yesterday")),
        json.dumps(dict(ok, review_id="d", reviewed_at="2026-10-30T12:00:00")),  # naive
        json.dumps(dict(ok, review_id="e", turn_index="0")),
        json.dumps(
            {k: v for k, v in ok.items() if k != "reviewer"} | {"review_id": "f"}
        ),
        json.dumps(dict(ok, review_id="g", schema_version=2)),
    ]
    with (run / "reviews.jsonl").open("a") as f:
        f.write("\n".join(bad) + "\n\n")
    r = load_reviews(run)
    assert [n for n, _ in r.malformed] == list(range(2, 2 + len(bad)))
    assert list(r.resolved) == [("c1", 0, "tool")]


def test_mismatched_evidence_is_stale_never_applied(run):
    rec = add(run)
    other = dict(rec, review_id="x", snapshot_sha256="0" * 64, dimension="answer")
    copied = dict(rec, review_id="y", eval_run_id="other-run", dimension="resolution")
    with (run / "reviews.jsonl").open("a") as f:
        f.write(json.dumps(other) + "\n" + json.dumps(copied) + "\n")
    r = load_reviews(run)
    assert r.stale == ["x", "y"] and list(r.resolved) == [("c1", 0, "tool")]


def test_tampered_snapshot_blocks_everything(run):
    add(run)
    (run / "captures.snapshot.jsonl").write_text("changed\n")
    with pytest.raises(RunError, match="modified"):
        load_reviews(run)
    with pytest.raises(RunError, match="modified"):
        add(run)


def test_unfrozen_run_rejected(run):
    (run / "captures.snapshot.sha256").unlink()
    with pytest.raises(RunError, match="not frozen"):
        add(run)


def test_unknown_turn_and_bad_input_rejected(run):
    with pytest.raises(RunError, match="not a turn"):
        append_review(run, new_record(run, "zz", 0, "tool", "correct", "me"))
    with pytest.raises(RunError, match="invalid review"):
        append_review(run, new_record(run, "c1", 0, "tool", "nope", "me"))
    assert not (run / "reviews.jsonl").exists()


def test_grader_and_human_stay_separate_and_disagreements_shown(run):
    add(run, "incorrect", dim="tool")  # grader: correct -> disagree
    add(run, "incorrect", dim="answer")  # grader: incorrect -> agree
    add(run, "correct", dim="resolution")  # no grader dimension
    add(run, "correct", dim="tool", turn=1)  # turn has no graded row
    r = load_reviews(run)
    graded = {("c1", 0): {"tool": "correct", "answer": "incorrect"}}
    assert [
        (d["dimension"], d["grader"], d["human"]) for d in disagreements(r, graded)
    ] == [("tool", "correct", "incorrect")]


def test_export_round_trip_via_cli(run, capsys):
    add(run, "incorrect", dim="tool", failure_layer="planner", notes="wrong tool")
    add(run, "correct", dim="answer", turn=1)
    root = run.parent
    assert main(["run-review-export", "--root", str(root), "--run", run.name,
                 "--allow-incomplete"]) == 0  # fmt: skip
    doc = json.loads((run / "reviews.resolved.json").read_text())
    assert (
        doc["snapshot_sha256"]
        == (run / "captures.snapshot.sha256").read_text().split()[0]
    )
    assert [d["dimension"] for d in doc["disagreements"]] == ["tool"]
    assert doc["unreviewed_turns"] == []
    assert load_human_verdicts(run) == {
        ("c1", 0): {"tool": "incorrect"},
        ("c1", 1): {"answer": "correct"},
    }
    assert materialize(run)["verdicts"][0]["failure_layer"] == "planner"
    # re-freezing different evidence invalidates the export
    (run / "captures.snapshot.jsonl").write_text("x\n")
    (run / "captures.snapshot.sha256").write_text(
        hashlib.sha256(b"x\n").hexdigest() + "  captures.snapshot.jsonl\n"
    )
    with pytest.raises(RunError, match="different evidence"):
        load_human_verdicts(run)


def test_add_cli_appends_without_touching_evidence(run, capsys):
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    assert main(["run-review-add", "--root", str(run.parent), "--run", run.name, "--allow-incomplete",
                 "--case", "c1", "--turn", "0", "--dimension", "tool", "--verdict", "correct",
                 "--reviewer", "me"]) == 0  # fmt: skip
    assert main(["run-review-add", "--root", str(run.parent), "--run", run.name, "--allow-incomplete",
                 "--case", "nope", "--turn", "0", "--dimension", "tool", "--verdict", "correct",
                 "--reviewer", "me"]) == 2  # fmt: skip
    after = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    assert set(after) - set(before) == {"reviews.jsonl"}
    assert all(after[k] == v for k, v in before.items())
    assert len(load_reviews(run).resolved) == 1

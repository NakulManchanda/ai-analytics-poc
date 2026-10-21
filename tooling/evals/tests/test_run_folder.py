"""run-start / run-freeze / run-grade."""

import hashlib
import json
import shutil
from pathlib import Path

from evals.cli import main

CAP = Path(__file__).resolve().parents[1] / "fixtures/captured"


def start(root, label="lab"):
    return main(["run-start", "--root", str(root), "--label", label])


def test_start_layout_and_label_guard(tmp_path, capsys):
    assert start(tmp_path) == 0
    run_id = capsys.readouterr().out.strip()
    assert run_id.startswith("lab-")
    assert (tmp_path / run_id / "logs").is_dir()
    m = json.loads((tmp_path / run_id / "manifest.json").read_text())
    assert m["label"] == "lab" and m["run_id"] == run_id and "cases" in m
    assert (tmp_path / ".current").read_text() == run_id
    assert start(tmp_path) == 2  # reuse blocked
    (tmp_path / run_id).rename(tmp_path / "lab-20200101T000000Z")
    assert start(tmp_path) == 2  # even with an older/different timestamp
    assert start(tmp_path, "bad label") == 2
    assert main(["run-start", "--root", str(tmp_path), "--label=-x"]) == 2


def test_prefix_label_not_blocked(tmp_path):
    (tmp_path / "foo-bar-20200101T000000Z").mkdir()
    assert start(tmp_path, "foo") == 0
    assert start(tmp_path, "foo-bar") == 2


def make_run(tmp_path):
    start(tmp_path, "r")
    return (tmp_path / ".current").read_text()


def test_freeze_exact_match_only(tmp_path):
    rid = make_run(tmp_path)
    d = tmp_path / "_capture" / "default"
    d.mkdir(parents=True)
    lines = [
        {"eval_run_id": rid, "n": 1},
        {"eval_run_id": rid + "-smoke", "n": 2},
        {"eval_run_id": "other", "n": 3},
        {"note": rid},
        {"eval_run_id": rid, "n": 4},
    ]
    (d / "captures.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n{broken " + rid + "\n"
    )
    assert main(["run-freeze", "--root", str(tmp_path)]) == 0
    run = tmp_path / rid
    snap = run / "captures.snapshot.jsonl"
    assert [json.loads(x)["n"] for x in snap.read_text().splitlines()] == [1, 4]
    assert not list(run.glob("*.tmp"))
    digest = hashlib.sha256(snap.read_bytes()).hexdigest()
    assert (run / "captures.snapshot.sha256").read_text() == (
        f"{digest}  captures.snapshot.jsonl\n"
    )
    assert main(["run-freeze", "--root", str(tmp_path)]) == 2  # immutable


def test_freeze_zero_matches_fails(tmp_path):
    make_run(tmp_path)
    d = tmp_path / "_capture" / "default"
    d.mkdir(parents=True)
    (d / "captures.jsonl").write_text('{"eval_run_id": "x"}\n')
    assert main(["run-freeze", "--root", str(tmp_path)]) == 2


def test_grade_one_report_per_conversation(tmp_path, capsys):
    rid = make_run(tmp_path)
    run = tmp_path / rid
    shutil.copy(CAP / "taxi-session.jsonl", run / "captures.snapshot.jsonl")
    shutil.copy(CAP / "matches.json", run / "matches.json")
    (run / "events").mkdir()
    capsys.readouterr()
    assert main(["run-grade", "--root", str(tmp_path)]) == 0
    assert "wrote 1 reports" in capsys.readouterr().out
    reports = list((run / "report").glob("conv_*.md"))
    assert len(reports) == 1 and "## Turns" in reports[0].read_text()


def test_missing_current_is_clear_error(tmp_path):
    assert main(["run-grade", "--root", str(tmp_path)]) == 2

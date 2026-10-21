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


def make_run(tmp_path, status="complete"):
    start(tmp_path, "r")
    rid = (tmp_path / ".current").read_text()
    (tmp_path / rid / "state.json").write_text(json.dumps({"status": status}))
    return rid


def test_freeze_exact_match_only(tmp_path):
    rid = make_run(tmp_path)
    d = tmp_path / "_capture" / "default"
    d.mkdir(parents=True)
    lines = [
        {"eval_run_id": rid, "run_id": "r1", "n": 1},
        {"eval_run_id": rid + "-smoke", "run_id": "r1", "n": 2},
        {"eval_run_id": "other", "run_id": "r1", "n": 3},
        {"note": rid},
        {"eval_run_id": rid, "run_id": "r1", "n": 4},
    ]
    (tmp_path / rid / "results.jsonl").write_text(json.dumps({"run_id": "r1"}) + "\n")
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


def test_freeze_drops_superseded_attempts(tmp_path):
    rid = make_run(tmp_path)
    run = tmp_path / rid
    (run / "results.superseded.jsonl").write_text(
        json.dumps({"run_id": "run_old"}) + "\n"
    )
    (run / "results.jsonl").write_text(json.dumps({"run_id": "run_new"}) + "\n")
    d = tmp_path / "_capture" / "default"
    d.mkdir(parents=True)
    lines = [
        {"eval_run_id": rid, "run_id": "run_old", "n": 1},
        {"eval_run_id": rid, "run_id": "run_new", "n": 2},
    ]
    (d / "captures.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    assert main(["run-freeze", "--root", str(tmp_path)]) == 0
    snap = run / "captures.snapshot.jsonl"
    assert [json.loads(x)["n"] for x in snap.read_text().splitlines()] == [2]


def test_freeze_zero_matches_fails(tmp_path):
    make_run(tmp_path)
    d = tmp_path / "_capture" / "default"
    d.mkdir(parents=True)
    (d / "captures.jsonl").write_text('{"eval_run_id": "x", "run_id": "r1"}\n')
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


def test_downstream_commands_refuse_unless_complete(tmp_path, capsys):
    rid = make_run(tmp_path, "running")
    base = tmp_path / "b.jsonl"
    base.write_text("")
    for argv in (
        ["run-freeze"],
        ["run-grade"],
        ["run-compare", "--baseline-results", str(base)],
        ["run-review"],
    ):
        assert main([*argv, "--root", str(tmp_path)]) == 2, argv
    assert "not 'complete'" in capsys.readouterr().err
    (tmp_path / rid / "state.json").unlink()  # no state at all also refuses
    assert main(["run-grade", "--root", str(tmp_path)]) == 2
    # override passes the guard (then fails later for a different reason)
    assert main(["run-grade", "--root", str(tmp_path), "--allow-incomplete"]) == 2
    assert "captures.snapshot.jsonl missing" in capsys.readouterr().err


def test_grade_writes_json_with_special_characters(tmp_path):
    rid = make_run(tmp_path)
    run = tmp_path / rid
    lines = []
    for n, l in enumerate((CAP / "taxi-session.jsonl").read_text().splitlines()):
        d = json.loads(l)
        if (
            d["record_type"] == "model_call"
            and d.get("question") == "Which pickup zones have the most trips?"
        ):
            d["question"] = Q
            if d.get("answer"):
                d["answer"] = A
        if d["record_type"] == "tool_call" and n == 1:
            d["arguments"]["note"] = ARG
        lines.append(json.dumps(d))
    (run / "captures.snapshot.jsonl").write_text("\n".join(lines) + "\n")
    shutil.copy(CAP / "matches.json", run / "matches.json")
    (run / "events").mkdir()
    assert main(["run-grade", "--root", str(tmp_path)]) == 0
    (conv,) = (run / "report").glob("conv_*.json")
    rows = json.loads(conv.read_text())["rows"]
    first = rows[0]
    assert first["question"] == Q and first["answer_text"] == A
    assert ARG in first["actual"] and first["tools"][0]["arguments"]["note"] == ARG
    assert first["case_id"] == "multi-001" and first["graded"] is True
    assert set(first) >= {
        "execution",
        "tool",
        "answer",
        "expected",
        "reasons",
        "cost_usd",
        "model_calls",
    }
    # downstream readers use the JSON, so the awkward characters survive
    from evals.runs import load_run_reports

    turns, _ = load_run_reports(run)
    assert turns[("multi-001", 0)]["question"] == Q


Q = "Zones | `top` <b>5</b> ?"
A = "JFK | `145240` <i>trips</i>"
ARG = "x|`y`<z>"


def test_freeze_allowlist_excludes_unrecorded_and_superseded(tmp_path, capsys):
    rid = make_run(tmp_path)
    run = tmp_path / rid
    (run / "results.jsonl").write_text(json.dumps({"run_id": "run_ok"}) + "\n")
    (run / "results.superseded.jsonl").write_text(
        json.dumps({"run_id": "run_old"}) + "\n"
    )
    (run / "submitted.jsonl").write_text(
        "".join(
            json.dumps({"run_id": r}) + "\n"
            for r in ("run_ok", "run_old", "run_orphan")
        )
    )
    d = tmp_path / "_capture" / "default"
    d.mkdir(parents=True)
    recs = [
        {"eval_run_id": rid, "run_id": "run_ok", "n": 1},
        {"eval_run_id": rid, "run_id": "run_old", "n": 2},
        {"eval_run_id": rid, "run_id": "run_orphan", "n": 3},
        {"eval_run_id": rid, "run_id": "run_unknown", "n": 4},
        {"eval_run_id": rid, "n": 5},
        {"eval_run_id": "other", "run_id": "run_ok", "n": 6},
    ]
    (d / "captures.jsonl").write_text("\n".join(map(json.dumps, recs)) + "\nnot json\n")
    capsys.readouterr()
    assert main(["run-freeze", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    snap = run / "captures.snapshot.jsonl"
    assert [json.loads(x)["n"] for x in snap.read_text().splitlines()] == [1]
    assert "kept 1" in out and "dropped 4" in out and "malformed 1" in out
    assert "run_orphan" in out

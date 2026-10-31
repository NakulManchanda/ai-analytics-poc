"""Shared fixtures for the eval tooling tests."""

import hashlib
import json

import pytest


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "r-20261030T120000Z"
    (d / "report").mkdir(parents=True)
    rows = [
        {
            "case_id": "c1",
            "turn_index": i,
            "question": f"q{i}",
            "terminal": "run.completed",
        }
        for i in (0, 1)
    ]
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    snap = d / "captures.snapshot.jsonl"
    snap.write_text('{"a": 1}\n')
    digest = hashlib.sha256(snap.read_bytes()).hexdigest()
    (d / "captures.snapshot.sha256").write_text(f"{digest}  {snap.name}\n")
    graded = [
        dict(case_id="c1", turn_index=0, graded=True, tool="correct", answer="incorrect",
             actual="x", expected=[], cost_usd=0.0, question="q0", execution="correct",
             reasons="", answer_text="a")
    ]  # fmt: skip
    (d / "report" / "conv.json").write_text(json.dumps({"rows": graded}))
    return d

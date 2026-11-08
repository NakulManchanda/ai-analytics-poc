"""run-reconcile: frozen capture vs LiteLLM spend rows (#226)."""

import json

import pytest

from evals.cli import main

RUN = "lab-20260101T000000Z"


def _capture(call_id: str, cost: float = 0.01) -> str:
    return json.dumps(
        {
            "schema_version": "capture.v1",
            "record_type": "model_call",
            "run_id": "run_1",
            "turn_id": "t1",
            "step": 1,
            "call_id": call_id,
            "cost_usd": cost,
            "cost_kind": "billed",
            "eval_run_id": RUN,
        }
    )


def _row(call_id: str, spend: float = 0.01) -> dict:
    return {
        "spend": spend,
        "request_tags": [f"eval_run_id:{RUN}"],
        "metadata": {"spend_logs_metadata": {"call_id": call_id, "attempt": 1}},
    }


def _run(tmp_path, calls: list[str], rows: list[dict] | None):
    run_dir = tmp_path / RUN
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"status": "complete"}))
    (run_dir / "captures.snapshot.jsonl").write_text(
        "\n".join(_capture(c) for c in calls) + "\n"
    )
    if rows is not None:
        (run_dir / "spend_logs.json").write_text(json.dumps(rows))
    return run_dir


def _reconcile(tmp_path) -> int:
    return main(["run-reconcile", "--root", str(tmp_path), "--run", RUN])


def test_clean_run_writes_report_and_exits_zero(tmp_path, capsys):
    run_dir = _run(tmp_path, ["c1"], [_row("c1")])
    assert _reconcile(tmp_path) == 0
    report = json.loads((run_dir / "report" / "spend_reconciliation.json").read_text())
    assert report["ok"] and report["reconciled_cost_usd"] == pytest.approx(0.01)


def test_cancel_orphan_is_added_to_reconciled_cost(tmp_path):
    run_dir = _run(tmp_path, ["c1"], [_row("c1"), _row("lost", 0.05)])
    assert _reconcile(tmp_path) == 0
    report = json.loads((run_dir / "report" / "spend_reconciliation.json").read_text())
    assert report["orphan_spend_usd"] == pytest.approx(0.05)
    assert report["reconciled_cost_usd"] == pytest.approx(0.06)


def test_missing_spend_row_fails_loudly(tmp_path, capsys):
    _run(tmp_path, ["c1", "c2"], [_row("c1")])
    assert _reconcile(tmp_path) == 4
    assert "capture call c2 has no spend row" in capsys.readouterr().err


def test_no_tagged_rows_fails_instead_of_passing_silently(tmp_path, capsys):
    _run(tmp_path, [], [{"spend": 1.0, "request_tags": []}])
    assert _reconcile(tmp_path) == 4
    assert "no tagged spend rows" in capsys.readouterr().err
    report = json.loads(
        (tmp_path / RUN / "report" / "spend_reconciliation.json").read_text()
    )
    assert report["ok"] is False


def test_missing_inputs_are_errors(tmp_path, capsys):
    _run(tmp_path, ["c1"], None)
    assert _reconcile(tmp_path) == 2
    assert "spend_logs.json missing" in capsys.readouterr().err

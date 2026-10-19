"""Real capture.v1 (no case_id/turn_index) ingest, explicit matching, session report."""

import json
from pathlib import Path

import pytest

from evals.dataset import load_cases
from evals.matching import MatchError, load_matches, resolve, suggest_candidates
from evals.session import SessionError, render_session_report
from evals.traces import TOOL_OK, build_run_views, build_turns, load_records

CAP = Path(__file__).resolve().parents[1] / "fixtures/captured"
TRACES, MATCHES = CAP / "taxi-session.jsonl", CAP / "matches.json"


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_real_capture_loads_without_case_id():
    records = load_records(TRACES)
    assert all(r.case_id is None for r in records)
    views = build_run_views(records)
    assert len(views) == 2
    first, second = sorted(views.values(), key=lambda v: v.started_at)
    assert first.conversation_id == second.conversation_id
    assert first.question and first.answer and first.tools[0].name == "query_taxi_data"
    assert "completed" in TOOL_OK and not first.errored


def test_build_turns_does_not_require_case_id_and_skips_unmatched():
    assert build_turns(load_records(TRACES)) == {}


def test_candidates_are_suggestions_only(cases):
    views = build_run_views(load_records(TRACES))
    cands = suggest_candidates(list(views.values()), cases)
    by_q = {c.question: c for c in cands}
    assert by_q["Which pickup zones have the most trips?"].suggested[-1] == (
        "multi-001",
        0,
    )
    assert (
        len(by_q["Which pickup zones have the most trips?"].suggested) > 1
    )  # ambiguous: human picks
    assert by_q["Compare that with the second highest zone"].suggested == [
        ("multi-001", 1)
    ]
    assert all(c.status == "candidate" for c in cands)


def test_unmatched_runs_stay_candidates(cases, tmp_path):
    views = build_run_views(load_records(TRACES))
    empty = tmp_path / "m.json"
    empty.write_text(json.dumps({"schema": "evals.matches.v1", "matches": []}))
    res = resolve(views, load_matches(empty, cases), cases)
    assert res.matched == {} and len(res.candidate_only) == 2


def test_pending_golden_not_graded_unless_provisional(cases):
    views = build_run_views(load_records(TRACES))
    matches = load_matches(MATCHES, cases)
    strict = resolve(views, matches, cases)
    assert strict.matched == {} and len(strict.unreviewed) == 2
    prov = resolve(views, matches, cases, provisional=True)
    assert set(prov.matched) == {("multi-001", 0), ("multi-001", 1)}
    assert len(prov.unreviewed) == 0 and prov.provisional


@pytest.mark.parametrize(
    "entry,msg",
    [
        ({"case_id": "nope", "turn_index": 0}, "unknown case"),
        ({"case_id": "multi-001", "turn_index": 9}, "turn_index"),
        ({"case_id": "multi-001", "turn_index": 0, "confirmed_by": ""}, "confirmed_by"),
    ],
)
def test_match_file_validation(cases, tmp_path, entry, msg):
    entry = {"run_id": "r1", "confirmed_by": "x", **entry}
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"schema": "evals.matches.v1", "matches": [entry]}))
    with pytest.raises(MatchError, match=msg):
        load_matches(f, cases)


def test_duplicate_target_rejected(cases, tmp_path):
    e = {"case_id": "multi-001", "turn_index": 0, "confirmed_by": "x"}
    f = tmp_path / "m.json"
    f.write_text(
        json.dumps(
            {
                "schema": "evals.matches.v1",
                "matches": [{"run_id": "a", **e}, {"run_id": "b", **e}],
            }
        )
    )
    with pytest.raises(MatchError, match="twice"):
        load_matches(f, cases)


def test_session_report_labels_origin_and_gates(cases):
    kwargs = dict(traces=TRACES, matches=MATCHES, cases=cases, claimed_origin=None)
    strict = render_session_report(**kwargs)
    assert "NOT LIVE" in strict and "candidate only" in strict.lower()
    assert "numeric_facts" not in strict  # nothing graded against a pending reference
    prov = render_session_report(**kwargs, provisional=True)
    assert "PROVISIONAL" in prov and "numeric_facts" in prov
    assert (
        "tokens" in prov.lower()
        and "latency" in prov.lower()
        and "cost" in prov.lower()
    )
    assert "live Bedrock" in prov


def test_claiming_live_on_scripted_capture_is_refused(cases):
    with pytest.raises(SessionError, match="refused"):
        render_session_report(TRACES, MATCHES, cases, claimed_origin="live")


def _rewrite(tmp_path, **fields):
    lines = [json.loads(x) for x in TRACES.read_text().splitlines()]
    for r in lines:
        r.update(fields)
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    return f


def test_live_only_when_every_record_records_live(cases, tmp_path):
    live = _rewrite(tmp_path, execution_mode="live")
    text = render_session_report(live, MATCHES, cases, claimed_origin="live")
    assert "origin: **LIVE**" in text and "NOT LIVE" not in text
    missing = _rewrite(tmp_path, execution_mode=None)
    assert "NOT LIVE" in render_session_report(missing, MATCHES, cases)


def test_fixture_is_recorded_as_scripted(cases):
    assert {r.execution_mode for r in load_records(TRACES)} == {"scripted"}
    assert "NOT LIVE" in render_session_report(TRACES, MATCHES, cases)


def test_multiple_conversations_rejected_unless_selected(cases, tmp_path):
    lines = [json.loads(x) for x in TRACES.read_text().splitlines()]
    other = [
        dict(
            r,
            conversation_id="conv_other",
            call_id=r["call_id"] + "x",
            run_id=r["run_id"] + "x",
        )
        for r in lines
    ]
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in lines + other) + "\n")
    with pytest.raises(SessionError, match="conversations"):
        render_session_report(f, MATCHES, cases)
    cid = lines[0]["conversation_id"]
    text = render_session_report(f, MATCHES, cases, conversation_id=cid)
    assert "runs: 2" in text
    with pytest.raises(SessionError, match="no records"):
        render_session_report(f, MATCHES, cases, conversation_id="nope")


def test_unknown_tokens_render_as_na(cases, tmp_path):
    lines = [json.loads(x) for x in TRACES.read_text().splitlines()]
    for r in lines:
        if r["record_type"] == "model_call":
            r.update(
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
                cost_kind="unknown",
            )
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    text = render_session_report(
        f, MATCHES, cases, claimed_origin=None, provisional=True
    )
    assert "n/a" in text and "incomplete" in text.lower()

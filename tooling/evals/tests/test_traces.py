from pathlib import Path

import pytest

from evals.traces import TraceError, load_records, load_turns

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_loads_synthetic_capture_v1_and_groups_turns():
    turns = load_turns(FIX / "baseline/traces.jsonl")
    v = turns[("det-004", 0)]
    assert v.tools[0].name == "query_taxi_data" and v.answer and v.question
    assert v.input_tokens and v.cost_usd and v.latency_ms
    assert ("multi-001", 1) in turns


def test_unknown_tokens_stay_none_not_zero():
    v = load_turns(FIX / "baseline/traces.jsonl")[("fail-004", 0)]
    assert v.input_tokens is None and v.cost_usd is None and v.errored


def test_duplicate_call_ids_are_idempotent(tmp_path):
    src = (FIX / "baseline/traces.jsonl").read_text().splitlines()[:3]
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(src + src) + "\n")
    assert len(load_records(f)) == 3


def test_rejects_other_schema_version(tmp_path):
    line = (
        (FIX / "baseline/traces.jsonl")
        .read_text()
        .splitlines()[0]
        .replace("capture.v1", "capture.v9")
    )
    f = tmp_path / "t.jsonl"
    f.write_text(line + "\n")
    with pytest.raises(TraceError):
        load_records(f)


def test_extra_producer_fields_are_ignored(tmp_path):
    import json

    rec = json.loads((FIX / "baseline/traces.jsonl").read_text().splitlines()[0])
    rec["future_field"] = 1
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps(rec) + "\n")
    assert len(load_records(f)) == 1

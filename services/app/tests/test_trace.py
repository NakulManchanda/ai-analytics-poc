from __future__ import annotations

import json

import pytest
from app.benchmarks.trace import TraceError, build_trace, parse_gateway_log, render_text

from services.app.scripts.trace_request import main as trace_main

T0 = 1_000_000.0


def _req(rid, step, hdr=None, **kw):
    return {
        "conversation_id": "c1",
        "turn_index": step,
        "status": "completed",
        "client_duration_ms": 900.0,
        "server_ttft_ms": 60.0,
        "tokens_in": 2100,
        "tokens_out": 32,
        "started_at": T0 + step * 2,
        "ended_at": T0 + step * 2 + 0.9,
        "request_id": rid,
        "workload_class": "interactive",
        "deadline_ms": 3000,
        "offered_concurrency": 1,
        "gateway_headers": (
            hdr
            if hdr is not None
            else {
                "x-place-decision": "worker_b",
                "x-placement-policy": "forced",
                "x-placement-reason": "forced",
                "x-intended-action": "recompute",
                "x-admit-decision": "accept",
                "x-queue-wait-ms": "5",
                "x-queue-decision": "dispatched",
            }
        ),
        **kw,
    }


def _log(rid, extra_hop=None):
    base = {
        "request_id": rid,
        "conversation_id": "c1",
        "agent_step": "2",
        "received_at": T0,
    }
    lines = [
        {
            **base,
            "stage": "admit",
            "ts": T0 + 0.004,
            "decision": "accept",
            "admission_inputs": {"kv": 0.3},
        },
        {
            **base,
            "stage": "place",
            "ts": T0 + 0.006,
            "chosen_worker": "worker_b",
            "prior_worker": "worker_a",
            "placement_policy": "forced",
            "placement_reason": "forced",
            "estimated_reusable_tokens": 0,
            "intended_action": "recompute",
            "snapshot_age": 0.4,
            "fallback": None,
        },
        {
            **base,
            "stage": "queue",
            "queue_enter": T0 + 0.007,
            "queue_dispatch": T0 + 0.012,
            "queue_wait_ms": 5,
        },
        {**base, "stage": "queue", "queue_release": T0 + 0.85},
    ]
    if extra_hop:
        lines.append({**base, "stage": "hop", **extra_hop})
    out = ["INFO:inference.gateway:" + json.dumps(d) for d in lines]
    out.insert(
        1,
        "INFO:inference.gateway:"
        + json.dumps({"request_id": "other", "stage": "place"}),
    )
    out.append("not json {")
    return "\n".join(out) + "\n"


def _run_dir(tmp_path, with_window=True):
    rows = [_req("r0", 0), _req("r1", 1), _req("r2", 2)]
    (tmp_path / "requests.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    levels = [
        {
            "offered_concurrency": 1,
            "prometheus_window": (
                {"delta": {"prefix_cache_hits": 10.0, "prompt_tokens": 99.0}}
                if with_window
                else {"delta": None}
            ),
        }
    ]
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "slos": {
                    "interactive_ttft_slo_ms": 100.0,
                    "default_e2e_slo_ms": 3500.0,
                },
                "levels": levels,
            }
        )
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"scenario": {"name": "s"}, "topology": "one A100"})
    )
    return tmp_path


def _st(trace, name):
    return next(s for s in trace["stages"] if s["stage"] == name)


def test_parse_gateway_log_filters_and_tolerates_prefix(tmp_path):
    lines = _log("r1").splitlines()
    assert len(parse_gateway_log(lines, "r1")) == 4


def test_full_join(tmp_path):
    run = _run_dir(tmp_path)
    (run / "gateway.log").write_text(_log("r1"))
    t = build_trace(run, "r1", run / "gateway.log")
    assert t["agent_step"] == 2 and t["chosen_worker"] == "worker_b"
    assert t["placement_policy"] == "forced" and t["slo"]["met"] is True
    assert _st(t, "admission")["duration_ms"] == 4.0
    assert _st(t, "placement")["duration_ms"] == 2.0
    assert _st(t, "placement")["details"]["prior_worker"] == "worker_a"
    assert _st(t, "queue")["duration_ms"] == 5
    eng = _st(t, "engine")
    assert eng["duration_ms"] == 838.0 and "NOT attributable" in eng["note"]
    assert eng["details"]["vllm_window_aggregate"]["prefix_cache_hits"] == 10.0
    assert _st(t, "ttft_decode")["details"]["decode_ms_derived"] == 840.0
    assert _st(t, "next_agent_step")["details"]["request_id"] == "r2"
    assert abs(_st(t, "next_agent_step")["duration_ms"] - 1100.0) < 1
    hop = _st(t, "hop")
    assert hop["status"] == "not_attempted" and "#133" in hop["note"]
    assert _st(t, "tool_call")["status"] == "unavailable"
    assert "SLO MET" in render_text(t)


def test_hop_only_when_confirmed(tmp_path):
    run = _run_dir(tmp_path)
    (run / "gateway.log").write_text(_log("r1", {"hop_result": "timeout"}))
    assert (
        _st(build_trace(run, "r1", run / "gateway.log"), "hop")["status"]
        == "attempted_not_confirmed"
    )
    (run / "gateway.log").write_text(
        _log(
            "r1",
            {
                "hop_result": "transferred",
                "transfer_ms": 12.0,
                "transferred_tokens": 2048,
            },
        )
    )
    hop = _st(build_trace(run, "r1", run / "gateway.log"), "hop")
    assert hop["status"] == "confirmed" and hop["duration_ms"] == 12.0


def test_missing_log_and_window_degrade_gracefully(tmp_path):
    run = _run_dir(tmp_path, with_window=False)
    t = build_trace(run, "r0")
    assert any("no gateway log" in u for u in t["unavailable"])
    assert _st(t, "admission")["status"] == "partial"
    assert _st(t, "placement")["details"]["chosen_worker"] == "worker_b"
    assert _st(t, "engine")["status"] == "unavailable"
    assert "unavailable" in _st(t, "engine")["note"]
    assert "Unavailable" in render_text(t)


def test_slo_not_met_when_ttft_over(tmp_path):
    run = _run_dir(tmp_path)
    rows = [
        json.loads(line) for line in (run / "requests.jsonl").read_text().splitlines()
    ]
    rows[1]["server_ttft_ms"] = 250.0
    (run / "requests.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    t = build_trace(run, "r1")
    assert (
        t["slo"]["met"] is False
        and t["slo"]["ttft_ok"] is False
        and "NOT MET" in render_text(t)
    )


def test_rejected_request_marks_later_stages_not_reached(tmp_path):
    run = _run_dir(tmp_path)
    rows = [
        _req(
            "r0",
            0,
            hdr={
                "x-admit-decision": "shed:deadline_unachievable",
                "x-place-decision": "none",
            },
            status="http_503",
        )
    ]
    (run / "requests.jsonl").write_text(json.dumps(rows[0]))
    log = {
        "request_id": "r0",
        "stage": "admit",
        "ts": T0 + 0.01,
        "received_at": T0,
        "code": "deadline_unachievable",
        "reason": "x",
    }
    (run / "gw.log").write_text(json.dumps(log))
    t = build_trace(run, "r0", run / "gw.log")
    assert (
        _st(t, "admission")["status"] == "rejected"
        and _st(t, "admission")["duration_ms"] == 10.0
    )
    for n in ("placement", "queue", "engine"):
        assert _st(t, n)["status"] == "not_reached"
    assert t["slo"]["met"] is False


def test_unknown_request_and_cli(tmp_path, capsys):
    run = _run_dir(tmp_path)
    with pytest.raises(TraceError):
        build_trace(run, "nope")
    assert trace_main(["--run-dir", str(run), "--request-id", "nope"]) == 2
    (run / "gateway.log").write_text(_log("r1"))
    out = tmp_path / "t.json"
    assert (
        trace_main(["--run-dir", str(run), "--request-id", "r1", "--json", str(out)])
        == 0
    )
    assert "Stage timeline" in capsys.readouterr().out
    assert json.loads(out.read_text())["request_id"] == "r1"
    assert trace_main(["--run-dir", str(run), "--list"]) == 0
    assert "r2" in capsys.readouterr().out

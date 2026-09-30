from __future__ import annotations

import csv
import io

from app.benchmarks.goodput import (
    Slos,
    is_good,
    summarize_turns,
    sweep_to_csv,
)
from app.benchmarks.replayer import TurnResult


def _t(**kw) -> TurnResult:
    base = dict(
        conversation_id="c",
        turn_index=0,
        prompt="p",
        status="completed",
        client_duration_ms=1000.0,
        server_ttft_ms=50.0,
        tokens_out=10,
    )
    base.update(kw)
    return TurnResult(**base)


def test_is_good_semantics():
    slos = Slos()
    assert is_good(_t(), slos)
    assert not is_good(_t(status="http_503"), slos)
    assert not is_good(_t(server_ttft_ms=150.0), slos)
    assert not is_good(_t(client_duration_ms=4000.0), slos)
    # per-turn deadline overrides the default E2E SLO
    assert is_good(_t(client_duration_ms=4000.0, deadline_ms=5000), slos)
    assert not is_good(_t(client_duration_ms=1000.0, deadline_ms=500), slos)
    # unmeasured TTFT is not good unless explicitly allowed
    assert not is_good(_t(server_ttft_ms=None), slos)
    assert is_good(_t(server_ttft_ms=None), Slos(require_ttft=False))
    # batch is judged on E2E only
    assert is_good(_t(workload_class="batch", server_ttft_ms=900.0), slos)


def test_summary_empty():
    s = summarize_turns([], 1.0, Slos())
    assert s["requests"] == 0
    assert s["good_requests_per_s"] == 0.0
    assert s["ttft_ms"]["p99"] == 0.0
    assert s["queue_wait_ms"]["p50"] == 0.0
    assert s["by_workload_class"] == {}


def test_summary_breakdowns_and_decisions():
    turns = [
        _t(
            workload_class="interactive",
            tenant_id="a",
            tokens_out=10,
            gateway_headers={
                "x-place-decision": "w1",
                "x-placement-policy": "least_loaded",
                "x-admit-decision": "accept",
                "x-queue-decision": "dispatched",
                "x-queue-wait-ms": "20",
            },
        ),
        _t(
            workload_class="batch",
            tenant_id="b",
            tokens_out=30,
            client_duration_ms=9000.0,
            gateway_headers={
                "x-place-decision": "w2",
                "x-placement-policy": "least_loaded",
                "x-admit-decision": "accept",
                "x-queue-decision": "dispatched",
                "x-queue-wait-ms": "40",
                "x-overflow": "true",
            },
        ),
        _t(
            status="http_429",
            tenant_id="a",
            tokens_out=0,
            gateway_headers={"x-admit-decision": "shed"},
        ),
    ]
    s = summarize_turns(turns, 2.0, Slos())
    assert s["requests"] == 3 and s["successful"] == 2
    assert s["requests_per_s"] == 1.5
    assert s["tokens_per_s"] == 20.0
    assert s["good_requests"] == 1 and s["good_requests_per_s"] == 0.5
    assert s["good_tokens_per_s"] == 5.0
    assert s["queue_wait_ms"]["p50"] == 30.0
    assert s["by_workload_class"]["batch"]["requests"] == 1
    assert s["by_tenant"]["a"]["requests"] == 2
    assert s["by_worker"]["w1"]["requests"] == 1
    assert s["by_placement_policy"]["least_loaded"]["requests"] == 2
    assert s["decisions"]["x-admit-decision"] == {"accept": 2, "shed": 1}
    assert s["decisions"]["x-overflow"] == {"true": 1}


def test_sweep_csv():
    rows = [
        {"offered_concurrency": 1, "requests_per_s": 1.0, "good_requests_per_s": 1.0},
        {"offered_concurrency": 4, "requests_per_s": 3.0, "good_requests_per_s": 0.5},
    ]
    out = list(csv.DictReader(io.StringIO(sweep_to_csv(rows))))
    assert out[1]["offered_concurrency"] == "4"
    assert out[1]["good_requests_per_s"] == "0.5"
    assert sweep_to_csv([]) == ""

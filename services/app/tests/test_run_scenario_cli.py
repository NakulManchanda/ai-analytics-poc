from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.benchmarks.metrics_scraper import (
    MetricsDelta,
)
from app.benchmarks.replayer import ReplaySummary, TurnResult

from services.app.scripts.run_scenario import (
    async_main,
    generate_markdown_report,
)


def test_generate_markdown_report():
    summary = ReplaySummary(
        scenario_name="test_scenario",
        total_conversations=1,
        total_turns=2,
        successful_turns=2,
        failed_turns=0,
        duration_seconds=1.5,
        requests_per_second=1.33,
        latency_ms={"p50": 100.0, "p90": 120.0, "p95": 125.0, "p99": 130.0},
        ttft_ms={"p50": 20.0, "p90": 25.0, "p95": 27.0, "p99": 30.0},
        total_prompt_tokens=500,
        total_completion_tokens=100,
        turn_results=[
            TurnResult(
                conversation_id="conv_1",
                turn_index=0,
                prompt="Which pickup zones have the most trips?",
                status="completed",
                client_duration_ms=100.0,
                tokens_in=250,
                tokens_out=50,
            ),
            TurnResult(
                conversation_id="conv_1",
                turn_index=1,
                prompt="What are the peak hours and busiest times for taxi rides in NYC?",
                status="completed",
                client_duration_ms=120.0,
                tokens_in=250,
                tokens_out=50,
            ),
        ],
    )
    delta = MetricsDelta(
        duration_seconds=1.5,
        prefix_cache_hits=1.0,
        prefix_cache_queries=2.0,
        prefix_cache_hit_rate_pct=50.0,
        prompt_tokens=500.0,
        generation_tokens=100.0,
        avg_ttft_seconds=0.02,
        avg_queue_time_seconds=0.005,
        gpu_cache_usage_post=0.35,
        avg_prompt_throughput=1200.0,
        avg_generation_throughput=200.0,
    )

    md = generate_markdown_report(
        summary=summary,
        metrics_delta=delta,
        target_url="http://127.0.0.1:8080",
        metrics_url="http://127.0.0.1:18001/metrics",
        strategy="manual",
        timestamp_str="2026-09-30T00:00:00Z",
    )
    assert "# Benchmark Evidence: `test_scenario`" in md
    assert "50.0%" in md
    assert "100.0" in md
    assert "Which pickup zones" in md


@pytest.mark.anyio
async def test_async_main_e2e(tmp_path: Path, monkeypatch):
    import httpx

    # Create dummy scenario in tmp_path
    scen_file = tmp_path / "dummy.json"
    scen_file.write_text(
        json.dumps(
            {
                "name": "dummy",
                "description": "Dummy scenario",
                "concurrency": 1,
                "strategy": "manual",
                "target_endpoint_type": "app_runs",
                "conversations": [
                    {
                        "conversation_id_prefix": "c1",
                        "turns": [
                            {"question": "Which pickup zones have the most trips?"}
                        ],
                    }
                ],
            }
        )
    )

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and url_str.endswith("/api/runs"):
            return httpx.Response(
                202,
                json={
                    "conversation_id": "c1",
                    "message_id": "m1",
                    "run_id": "r1",
                    "events_url": "/api/runs/r1/events",
                },
            )
        elif "/events" in url_str:
            completed_str = json.dumps(
                {
                    "status": "completed",
                    "input_tokens": 100,
                    "output_tokens": 20,
                }
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"event: run.completed\ndata: {completed_str}\n\n",
            )
        elif "/metrics" in url_str:
            return httpx.Response(
                200,
                text="vllm:prefix_cache_hits_total 10.0\nvllm:prefix_cache_queries_total 20.0\n",
            )
        return httpx.Response(404)

    # Monkeypatch httpx.AsyncClient to use mock transport
    original_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(mock_handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_factory)

    class MockArgs:
        scenario = str(scen_file)
        target_url = "http://test:8080"
        metrics_url = "http://test:18001/metrics"
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")

    res = await async_main(MockArgs())
    assert res == 0

    # Verify generated evidence
    ev_files = list((tmp_path / "evidence").glob("dummy_manual_*.json"))
    assert len(ev_files) == 1
    with open(ev_files[0]) as f:
        data = json.load(f)
        assert data["summary"]["successful_turns"] == 1
        assert data["metrics_delta"] is not None
        # pre and post had same values -> delta=0
        assert data["metrics_delta"]["prefix_cache_hit_rate_pct"] == 0.0


@pytest.mark.anyio
async def test_async_main_invalid_concurrency_fails_fast(tmp_path: Path):
    from pydantic import ValidationError

    scen_file = tmp_path / "dummy_invalid.json"
    scen_file.write_text(
        json.dumps(
            {
                "name": "dummy_inv",
                "description": "Dummy scenario",
                "concurrency": 2,
                "strategy": "manual",
                "target_endpoint_type": "app_runs",
                "conversations": [
                    {
                        "conversation_id_prefix": "c1",
                        "turns": [
                            {"question": "Which pickup zones have the most trips?"}
                        ],
                    }
                ],
            }
        )
    )

    class MockInvalidArgs:
        scenario = str(scen_file)
        target_url = "http://test:8080"
        metrics_url = None
        concurrency = 0  # Invalid concurrency should fail validation
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")

    with pytest.raises(ValidationError):
        await async_main(MockInvalidArgs())


@pytest.mark.anyio
async def test_async_main_writes_run_directory_with_sweep(tmp_path: Path, monkeypatch):
    import httpx

    scen_file = tmp_path / "gw.json"
    scen_file.write_text(
        json.dumps(
            {
                "name": "gw",
                "description": "gateway scenario",
                "target_endpoint_type": "gateway_chat",
                "conversations": [
                    {
                        "turns": [{"question": "q", "tenant_id": "t1"}],
                    },
                    {"turns": [{"question": "q2"}]},
                ],
            }
        )
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/metrics" in str(request.url):
            return httpx.Response(200, text="vllm:prefix_cache_hits_total 1.0\n")
        return httpx.Response(
            200,
            headers={
                "x-place-decision": "w1",
                "x-policy-override-applied": "p2c",
                "x-admission-mode": "off",
            },
            json={"id": "i", "usage": {"prompt_tokens": 1, "completion_tokens": 4}},
        )

    original = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return original(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setenv("MODEL_REVISION", "rev123")

    class Args:
        scenario = str(scen_file)
        target_url = "http://gw:18080"
        metrics_url = "http://gw:18001/metrics"
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")
        sweep_concurrency = "1,2"
        label = "least_loaded"
        ttft_slo_ms = 100.0
        e2e_slo_ms = 3500.0
        topology = None
        engine_flags = "--max-num-seqs 8"
        policy_override = "p2c"
        admission_mode = "off"

    assert await async_main(Args()) == 0
    runs = [p for p in (tmp_path / "evidence").iterdir() if p.is_dir()]
    assert len(runs) == 1
    run = runs[0]
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["scenario"]["name"] == "gw"
    assert len(manifest["scenario"]["sha256"]) == 64
    assert manifest["policy_under_test"] == "least_loaded"
    assert (
        manifest["policy_override_requested"],
        manifest["admission_mode_requested"],
    ) == (
        "p2c",
        "off",
    )
    assert manifest["policy_override_verified"] is True
    assert manifest["admission_mode_verified"] is True
    assert manifest["control_unverified_turns"] == 0
    assert manifest["control_observations"]["policy_override"]["matched"] > 0
    assert manifest["slos"]["interactive_ttft_slo_ms"] == 100.0
    assert "A100" in manifest["topology"]
    assert manifest["model_revision"] == "rev123"
    assert manifest["tokenizer_revision"] == "unknown"
    assert manifest["engine_flags"] == "--max-num-seqs 8"
    assert "MUST NOT be attributed" in manifest["evidence_scope"]
    assert manifest["gateway_base_url"] == "http://gw:18080"
    assert manifest["started_at"] and manifest["ended_at"]
    lines = (run / "requests.jsonl").read_text().splitlines()
    assert len(lines) == 4
    rec = json.loads(lines[0])
    assert rec["offered_concurrency"] in (1, 2)
    assert rec["gateway_headers"]["x-place-decision"] == "w1"
    summary = json.loads((run / "summary.json").read_text())
    assert [lv["offered_concurrency"] for lv in summary["levels"]] == [1, 2]
    assert summary["levels"][0]["prometheus_window"]["scope"] == "window"
    assert (run / "sweep.csv").read_text().startswith("offered_concurrency")
    assert json.loads((run / "sweep.json").read_text())[1]["offered_concurrency"] == 2


@pytest.mark.anyio
async def test_sweep_exit_nonzero_when_earlier_level_fails(tmp_path: Path, monkeypatch):
    import httpx

    scen = tmp_path / "s.json"
    scen.write_text(
        json.dumps(
            {
                "name": "s",
                "description": "d",
                "target_endpoint_type": "gateway_chat",
                "conversations": [{"turns": [{"question": "q"}]}],
            }
        )
    )
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "x"})
        return httpx.Response(200, json={"id": "i", "usage": {}})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: original(
            *a, **{**kw, "transport": httpx.MockTransport(handler)}
        ),
    )

    class Args:
        scenario = str(scen)
        target_url = "http://gw:18080"
        metrics_url = None
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "ev")
        sweep_concurrency = "1,2"
        gateway_stream = False

    assert await async_main(Args()) == 1


def test_markdown_and_json_render_unavailable_tokens_as_na():
    summary = ReplaySummary(
        scenario_name="s",
        total_conversations=1,
        total_turns=1,
        successful_turns=1,
        failed_turns=0,
        duration_seconds=1.0,
        requests_per_second=1.0,
        latency_ms={},
        ttft_ms={},
        total_prompt_tokens=None,
        total_completion_tokens=None,
        tokens_measured_turns=0,
        tokens_unmeasured_turns=1,
        turn_results=[
            TurnResult(
                conversation_id="c",
                turn_index=0,
                prompt="p",
                status="completed",
                client_duration_ms=1.0,
            )
        ],
    )
    md = generate_markdown_report(summary, None, "u", None, "manual", "t")
    assert "| **Prompt Tokens** | n/a |" in md
    assert "| **Completion Tokens** | n/a |" in md
    assert "| 0 / 1 |" in md
    assert "| n/a | n/a |" in md
    dumped = summary.model_dump()
    assert dumped["total_prompt_tokens"] is None
    assert dumped["tokens_unmeasured_turns"] == 1


@pytest.mark.anyio
async def test_controls_ignored_by_gateway_abort_run_without_evidence(
    tmp_path: Path, monkeypatch
):
    """Gateway with ALLOW_EXPERIMENT_CONTROLS off: no applied headers -> run must not look OK."""
    import httpx

    scen_file = tmp_path / "gw.json"
    scen_file.write_text(
        json.dumps(
            {
                "name": "gw",
                "description": "d",
                "target_endpoint_type": "gateway_chat",
                "conversations": [{"turns": [{"question": "q"}, {"question": "q2"}]}],
            }
        )
    )
    original = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"x-place-decision": "w1"}, json={"id": "i"}
            )
        )
        return original(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    class Args:
        scenario = str(scen_file)
        target_url = "http://gw:18080"
        metrics_url = None
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")
        policy_override = "least_loaded"
        admission_mode = None

    assert await async_main(Args()) != 0
    assert not (tmp_path / "evidence").exists() or not any(
        (tmp_path / "evidence").iterdir()
    )


def _controls_args(tmp_path: Path, scen: dict):
    scen_file = tmp_path / "s.json"
    scen_file.write_text(json.dumps(scen))

    class Args:
        scenario = str(scen_file)
        target_url = "http://gw:18080"
        metrics_url = None
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")
        policy_override = "p2c"
        admission_mode = None

    return Args


@pytest.mark.anyio
async def test_controls_with_app_runs_fail_fast(tmp_path: Path):
    args = _controls_args(
        tmp_path,
        {
            "name": "app",
            "description": "d",
            "target_endpoint_type": "app_runs",
            "conversations": [{"turns": [{"question": "q"}]}],
        },
    )
    args.target_url = "http://app:8080"
    assert await async_main(args) == 2
    assert not (tmp_path / "evidence").exists()


@pytest.mark.anyio
async def test_connection_failure_never_reports_control_verified(
    tmp_path: Path, monkeypatch
):
    import httpx

    original = httpx.AsyncClient

    def refuse(request):
        raise httpx.ConnectError("down")

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(refuse)
        return original(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    args = _controls_args(
        tmp_path,
        {
            "name": "gw",
            "description": "d",
            "target_endpoint_type": "gateway_chat",
            "conversations": [{"turns": [{"question": "q"}]}],
        },
    )
    assert await async_main(args) == 1
    manifest = json.loads(
        next((tmp_path / "evidence").glob("*/manifest.json")).read_text()
    )
    assert manifest["policy_override_verified"] is False
    assert manifest["control_observations"]["policy_override"] == {
        "matched": 0,
        "mismatched": 0,
    }


def test_markdown_report_includes_placement_reason_by_turn():
    summary = ReplaySummary(
        scenario_name="s",
        total_conversations=1,
        total_turns=2,
        successful_turns=2,
        failed_turns=0,
        duration_seconds=1.0,
        requests_per_second=2.0,
        latency_ms={},
        ttft_ms={},
        turn_results=[
            TurnResult(
                conversation_id="c",
                turn_index=i,
                prompt="p",
                status="completed",
                client_duration_ms=1.0,
                gateway_headers={"x-placement-reason": r},
            )
            for i, r in enumerate(["prefix_affinity", "prefix_overlap_low"])
        ],
    )
    md = generate_markdown_report(summary, None, "http://gw", None, "manual", "t")
    assert "Placement reasons by turn" in md
    assert "| 1 | prefix_affinity: 1 |" in md
    assert "| 2 | prefix_overlap_low: 1 |" in md


@pytest.mark.anyio
async def test_system_prefix_record_exact_count_and_failure():
    import httpx

    from services.app.scripts.run_scenario import system_prefix_record

    seen = []

    def ok(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"count": 77, "tokens": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as client:
        rec = await system_prefix_record(
            "abcdefgh" * 10, "http://gw/tokenize", "m", client
        )
    assert seen == [("http://gw/tokenize", {"model": "m", "prompt": "abcdefgh" * 10})]
    assert rec["prefix_chars"] == 80 and rec["estimated_tokens_chars_div_4"] == 20
    assert rec["exact_tokens"] == 77 and rec["exact_unavailable_reason"] is None
    assert "estimate" in rec["estimate_method"] and "exact" in rec["exact_method"]

    def boom(request):
        raise httpx.ConnectError("nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        rec = await system_prefix_record("abcd", "http://gw/tokenize", "m", client)
    assert (
        rec["exact_tokens"] is None
        and "tokenize_call_failed" in rec["exact_unavailable_reason"]
    )
    assert rec["estimated_tokens_chars_div_4"] == 1

    def bad(request):
        return httpx.Response(500, text="x")

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as client:
        rec = await system_prefix_record("abcd", "http://gw/tokenize", "m", client)
    assert rec["exact_unavailable_reason"] == "tokenize_http_500"
    assert await system_prefix_record(None, "u", "m", None) is None


@pytest.mark.anyio
async def test_manifest_records_system_prefix_estimate_and_exact(
    tmp_path: Path, monkeypatch
):
    import httpx

    scen = tmp_path / "s.json"
    scen.write_text(
        json.dumps(
            {
                "name": "gw",
                "description": "d",
                "target_endpoint_type": "gateway_chat",
                "system_prefix": "x" * 40,
                "conversations": [{"turns": [{"question": "q"}]}],
            }
        )
    )

    def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 9})
        return httpx.Response(200, json={"id": "i"})

    original = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return original(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    class Args:
        scenario = str(scen)
        target_url = "http://gw:18080"
        metrics_url = None
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")
        tokenize_url = None  # default: derived from the gateway base URL

    assert await async_main(Args()) == 0
    manifest = json.loads(
        next((tmp_path / "evidence").glob("*/manifest.json")).read_text()
    )
    sp = manifest["system_prefix"]
    assert (
        sp["prefix_chars"],
        sp["estimated_tokens_chars_div_4"],
        sp["exact_tokens"],
    ) == (
        40,
        10,
        9,
    )
    assert sp["tokenize_url"] == "http://gw:18080/tokenize"


def _plain_router_scenario(tmp_path: Path) -> Path:
    f = tmp_path / "dyn.json"
    f.write_text(
        json.dumps(
            {
                "name": "dyn",
                "description": "d",
                "target_endpoint_type": "gateway_chat",
                "conversations": [{"turns": [{"question": "q"}, {"question": "q2"}]}],
            }
        )
    )
    return f


@pytest.mark.anyio
async def test_router_label_non_gateway_rejects_controls(tmp_path: Path):
    args = _controls_args(
        tmp_path,
        {
            "name": "dyn",
            "description": "d",
            "target_endpoint_type": "gateway_chat",
            "conversations": [{"turns": [{"question": "q"}]}],
        },
    )
    args.router_label = "dynamo-kv"
    assert await async_main(args) == 2
    assert not (tmp_path / "evidence").exists()


@pytest.mark.anyio
async def test_non_gateway_arm_tolerates_missing_decision_headers(
    tmp_path: Path, monkeypatch
):
    import httpx

    scen_file = _plain_router_scenario(tmp_path)
    original = httpx.AsyncClient

    def factory(*a, **kw):
        sse = (
            'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n"
        )
        kw["transport"] = httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse
            )
        )
        return original(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    class Args:
        scenario = str(scen_file)
        target_url = "http://dynamo:8000"
        metrics_url = None
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path / "evidence")
        policy_override = None
        admission_mode = None
        router_label = "dynamo-kv"
        label = "kv-aware"

    assert await async_main(Args()) == 0
    manifest = json.loads(
        next((tmp_path / "evidence").rglob("manifest.json")).read_text()
    )
    assert manifest["router_label"] == "dynamo-kv"
    assert manifest["gateway_decision_headers_expected"] is False
    assert manifest["turns_with_decision_headers"] == 0
    assert manifest["policy_override_verified"] is None
    assert manifest["admission_mode_verified"] is None

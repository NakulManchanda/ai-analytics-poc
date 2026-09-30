from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from app.benchmarks import e5_locality as e5
from app.benchmarks.replayer import ControlNotApplied, ScenarioReplayer
from app.scenarios.loader import load_scenario, validate_catalogue_alignment
from app.scenarios.models import ScenarioConfig, ScenarioConversation, ScenarioTurn

SCEN = Path("config/scenarios")


def test_committed_e5_scenarios_match_generator():
    built = e5.build_scenarios()
    assert len(built) == 13
    for name, data in built.items():
        on_disk = json.loads((SCEN / f"{name}.json").read_text())
        assert (
            on_disk == data
        ), f"{name} drifted: regenerate with python -m app.benchmarks.e5_locality"


def test_prefix_sizes_and_labels():
    for label, size in e5.SIZES.items():
        cfg = load_scenario(f"e5_recompute_control_{label}", scenarios_dir=SCEN)
        assert e5.estimated_tokens(cfg.system_prefix) == size
        assert "SYNTHETIC" in cfg.description and "#133" in cfg.description
    assert list(e5.SIZES) == ["1k", "2k", "4k", "7k"]


def test_prefixes_are_unique_per_case():
    a = e5.synthetic_prefix("a", 1024)
    b = e5.synthetic_prefix("b", 1024)
    assert a[:40] != b[:40]


def _forced(cfg: ScenarioConfig) -> list[list[str | None]]:
    return [
        [t.force_worker or c.force_worker for t in c.turns] for c in cfg.conversations
    ]


def test_force_patterns_per_case():
    for label in e5.SIZES:
        assert _forced(
            load_scenario(f"e5_local_reuse_{label}", scenarios_dir=SCEN)
        ) == [["worker_a", "worker_a"]]
        c = load_scenario(f"e5_recompute_control_{label}", scenarios_dir=SCEN)
        assert (
            _forced(c) == [["worker_a", "worker_b"]]
            and c.treatment == "e5_recompute_control"
        )
        assert _forced(
            load_scenario(f"e5_destination_hit_{label}", scenarios_dir=SCEN)
        ) == [
            ["worker_b"],
            ["worker_a", "worker_b"],
        ]
    ev = load_scenario("e5_local_eviction_4k", scenarios_dir=SCEN)
    assert ev.concurrency == 2 and ev.conversations[0].turns[1].delay_seconds > 0
    assert (
        all(f == ["worker_a"] for f in _forced(ev)[1:]) and len(ev.conversations) == 11
    )
    assert "UNVERIFIED" in ev.description


def test_e5_scenarios_fit_context_and_catalogue():
    window, margin = 8192, 256
    for p in SCEN.glob("e5_*.json"):
        cfg = load_scenario(p.stem, scenarios_dir=SCEN)
        assert cfg.target_endpoint_type == "gateway_chat" and cfg.uses_force_worker
        assert validate_catalogue_alignment(cfg) == []
        for conv in cfg.conversations:
            prefix = conv.system_prefix or cfg.system_prefix
            ptok = len(json.dumps([{"role": "system", "content": prefix}])) // 4
            prior = 0
            for t in conv.turns:
                q = len(t.question) // 4 + 8
                assert ptok + prior + q + cfg.max_tokens <= window - margin, (
                    p.stem,
                    ptok,
                )
                prior += q + cfg.max_tokens


def test_no_transfer_backend_named_in_e5_scenarios():
    for p in SCEN.glob("e5_*.json"):
        text = p.read_text().lower()
        assert "mooncake" not in text and "lmcache" not in text


def _cfg(turns, **kw):
    return ScenarioConfig(
        name="f",
        description="d",
        target_endpoint_type="gateway_chat",
        conversations=[ScenarioConversation(turns=turns, **kw)],
    )


def _handler(sent, echo):
    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("x-force-worker"))
        hdrs = echo(request.headers.get("x-force-worker"), len(sent))
        return httpx.Response(200, headers=hdrs, json={"id": "x"})

    return handler


async def _run(cfg, handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rp = ScenarioReplayer(cfg, "http://gw", client=client, gateway_stream=False)
        try:
            return rp, await rp.run()
        except ControlNotApplied as exc:
            return rp, exc


def _honored(w, n):
    if w is None:  # e.g. the /tokenize call, not a forced turn
        return {}
    return {"x-place-decision": w, "x-placement-policy": "forced"}


@pytest.mark.anyio
async def test_force_header_sent_per_turn_overrides_conversation_default():
    cfg = _cfg(
        [
            ScenarioTurn(question="a"),
            ScenarioTurn(question="b", force_worker="worker_b"),
        ],
        force_worker="worker_a",
    )
    sent: list = []
    rp, s = await _run(cfg, _handler(sent, _honored))
    assert sent == ["worker_a", "worker_b"]
    assert [t.force_worker for t in s.turn_results] == ["worker_a", "worker_b"]
    assert all(t.status == "completed" for t in s.turn_results)
    assert rp.control_observations == {"force_worker": [2, 0]}


@pytest.mark.anyio
async def test_unforced_turns_send_no_header():
    sent: list = []
    rp, s = await _run(
        _cfg([ScenarioTurn(question="a")]), _handler(sent, lambda w, n: {})
    )
    assert sent == [None] and rp.control_observations == {}


@pytest.mark.anyio
async def test_force_ignored_first_response_aborts_fail_fast():
    cfg = _cfg(
        [
            ScenarioTurn(question="a", force_worker="worker_b"),
            ScenarioTurn(question="b"),
        ]
    )
    sent: list = []
    # gateway without ALLOW_FORCED_PLACEMENT picks normally (even the same worker by chance)
    rp, out = await _run(
        cfg,
        _handler(
            sent, lambda w, n: {"x-place-decision": w, "x-placement-policy": "p2c"}
        ),
    )
    assert isinstance(out, ControlNotApplied) and "ALLOW_FORCED_PLACEMENT" in str(out)
    assert len(sent) == 1


@pytest.mark.anyio
async def test_force_lost_mid_run_marks_only_that_turn():
    cfg = _cfg(
        [ScenarioTurn(question="a"), ScenarioTurn(question="b")],
        force_worker="worker_a",
    )
    sent: list = []

    def echo(w, n):
        return _honored(w, n) if n == 1 else {"x-place-decision": "worker_b"}

    rp, s = await _run(cfg, _handler(sent, echo))
    assert [t.status for t in s.turn_results] == ["completed", "control_not_applied"]
    assert rp.control_observations["force_worker"] == [1, 1]


@pytest.mark.anyio
async def test_shed_before_placement_is_not_a_control_failure():
    cfg = _cfg([ScenarioTurn(question="a", force_worker="worker_a")])

    async def handler(request):
        return httpx.Response(
            429, headers={"x-place-decision": "none"}, json={"error": "x"}
        )

    rp, s = await _run(cfg, handler)
    assert s.turn_results[0].status == "http_429"
    assert rp.control_observations == {
        "force_worker": [0, 0]
    }  # unobserved, not mismatched


def test_uses_force_worker_property():
    assert not _cfg([ScenarioTurn(question="a")]).uses_force_worker
    assert _cfg([ScenarioTurn(question="a")], force_worker="worker_a").uses_force_worker
    with pytest.raises(ValueError):
        ScenarioTurn(question="a", force_worker="worker_c")


@pytest.mark.anyio
async def test_manifest_records_force_fields(tmp_path, monkeypatch):
    from services.app.scripts.run_scenario import async_main

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: original(
            *a, **{**kw, "transport": httpx.MockTransport(_handler([], _honored))}
        ),
    )

    class Args:
        scenario = "e5_recompute_control_1k"
        target_url = "http://gw:18080"
        metrics_url = None
        concurrency = None
        strategy = None
        endpoint_type = None
        timeout = 10.0
        no_sse = False
        output_dir = str(tmp_path)
        sweep_concurrency = None
        label = "e5-control"
        ttft_slo_ms = 100.0
        e2e_slo_ms = 3500.0
        topology = None
        engine_flags = None
        gateway_stream = False

    await async_main(Args())
    run = next(p for p in tmp_path.iterdir() if p.is_dir())
    m = json.loads((run / "manifest.json").read_text())
    assert m["treatment"] == "e5_recompute_control"
    assert m["force_worker_requested"] == ["worker_a", "worker_b"]
    assert m["force_worker_verified"] is True
    assert m["control_observations"]["force_worker"] == {"matched": 2, "mismatched": 0}
    rows = [
        json.loads(line) for line in (run / "requests.jsonl").read_text().splitlines()
    ]
    assert [r["force_worker"] for r in rows] == ["worker_a", "worker_b"]

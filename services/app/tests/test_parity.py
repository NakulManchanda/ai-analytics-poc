from __future__ import annotations

import copy
import json
from argparse import Namespace
from pathlib import Path

import httpx
import pytest
from app.benchmarks.parity import check_arms, check_run, main

from services.app.scripts.run_scenario import async_main

SLOS = {"interactive_ttft_slo_ms": 100.0, "default_e2e_slo_ms": 3500.0}


def manifest(label: str = "gateway", **over):
    dyn = label != "gateway"
    m = {
        "router_label": label,
        "scenario": {"sha256": "abc"},
        "slos": SLOS,
        "max_tokens": 128,
        "topology": "two workers",
        "model_revision": "m1",
        "tokenizer_revision": "t1",
        "chat_template_revision": "c1",
        "engine_flags": "--enable-prefix-caching",
        "vllm_version": "0.11.0",
        "kv_block_size": "16",
        "dynamo_version": "1.0.1" if dyn else "n/a",
        "dynamo_kv_block_size": "16" if dyn else "n/a",
        "offered_concurrency_levels": [8],
    }
    m.update(over)
    return m


def arms():
    return {"A": manifest(), "B": manifest(), "C": manifest("dynamo-kv")}


def test_complete_matching_set_accepted():
    assert check_arms(arms()) == []


def test_unknown_on_all_arms_rejected():
    a = {
        k: manifest(
            k_label,
            model_revision="unknown",
            tokenizer_revision="unknown",
            chat_template_revision="unknown",
            engine_flags="unknown",
        )
        for k, k_label in (("A", "gateway"), ("B", "gateway"), ("C", "dynamo-kv"))
    }
    problems = check_arms(a)
    assert any("model_revision" in p for p in problems)
    assert any("engine_flags" in p for p in problems)


@pytest.mark.parametrize("field", ["kv_block_size", "dynamo_kv_block_size"])
def test_missing_block_size_rejected(field):
    a = arms()
    a["C"].pop(field)
    assert any(field in p for p in check_arms(a))


def test_dynamo_block_size_must_be_number_and_equal():
    a = arms()
    a["C"]["dynamo_kv_block_size"] = "n/a"
    assert check_arms(a)
    a["C"]["dynamo_kv_block_size"] = "32"
    assert any("!=" in p for p in check_arms(a))


def test_mismatched_versions_rejected():
    a = arms()
    a["C"]["vllm_version"] = "0.12.0"
    assert any("vllm_version differs" in p for p in check_arms(a))
    a = arms()
    a["D"] = manifest("dynamo-kv", dynamo_version="1.5.0")
    assert any("dynamo_version differs" in p for p in check_arms(a))


def test_dynamo_version_na_rejected_for_dynamo_arm_only():
    assert check_run(manifest()) == []
    assert check_run(manifest("dynamo-kv", dynamo_version="n/a"))
    assert check_run(manifest(dynamo_version=None))  # must be explicit


def test_manifest_cli(tmp_path: Path):
    paths = []
    for name, m in arms().items():
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(m))
        paths.append(str(p))
    assert main(paths) == 0
    bad = copy.deepcopy(arms()["C"])
    bad["engine_flags"] = "different"
    (tmp_path / "C.json").write_text(json.dumps(bad))
    assert main(paths) == 1


SCEN = {
    "name": "arm",
    "description": "d",
    "target_endpoint_type": "gateway_chat",
    "conversations": [{"turns": [{"question": "q"}, {"question": "q2"}]}],
}
META = dict(
    model_revision="m1",
    tokenizer_revision="t1",
    chat_template_revision="c1",
    engine_flags="--f",
    vllm_version="0.11.0",
    kv_block_size="16",
)


def _patch_transport(monkeypatch, echo_policy: bool):
    original = httpx.AsyncClient
    sse = (
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        h = {"content-type": "text/event-stream"}
        if echo_policy and (v := request.headers.get("x-placement-policy-override")):
            h["x-policy-override-applied"] = v
        return httpx.Response(200, headers=h, content=sse)

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return original(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _args(tmp_path: Path, scen: Path, out: str, **over):
    base = dict(
        scenario=str(scen),
        target_url="http://x:18080",
        metrics_url=None,
        concurrency=None,
        strategy=None,
        endpoint_type=None,
        timeout=10.0,
        no_sse=False,
        output_dir=str(tmp_path / out),
        policy_override=None,
        admission_mode=None,
        router_label=None,
        label=None,
        **META,
    )
    base.update(over)
    return Namespace(**base)


@pytest.mark.anyio
async def test_three_arms_share_scenario_hash_but_not_execution(
    tmp_path: Path, monkeypatch
):
    scen = tmp_path / "s.json"
    scen.write_text(json.dumps(SCEN))
    _patch_transport(monkeypatch, echo_policy=True)
    arm_args = {
        "A": dict(policy_override="least_loaded", label="least_loaded"),
        "B": dict(policy_override="prefix_then_load", label="prefix_then_load"),
        "C": dict(
            router_label="dynamo-kv",
            label="kv",
            dynamo_version="1.0.1",
            dynamo_kv_block_size="16",
        ),
    }
    manifests = {}
    for arm, over in arm_args.items():
        args = _args(tmp_path, scen, arm, require_parity=True, **over)
        if arm != "C":
            args.dynamo_version = args.dynamo_kv_block_size = "n/a"
        assert await async_main(args) == 0
        manifests[arm] = json.loads(
            next((tmp_path / arm).rglob("manifest.json")).read_text()
        )
    assert len({m["scenario"]["sha256"] for m in manifests.values()}) == 1
    assert len({m["execution"]["sha256"] for m in manifests.values()}) == 3
    assert manifests["C"]["router_label"] == "dynamo-kv"
    assert manifests["A"]["execution"]["policy_override"] == "least_loaded"
    assert check_arms(manifests) == []


@pytest.mark.anyio
async def test_require_parity_fails_fast_without_evidence(tmp_path: Path):
    scen = tmp_path / "s.json"
    scen.write_text(json.dumps(SCEN))
    args = _args(tmp_path, scen, "ev", require_parity=True)  # dynamo fields unset
    args.kv_block_size = None
    assert await async_main(args) == 2
    assert not (tmp_path / "ev").exists()

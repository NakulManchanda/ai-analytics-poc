from __future__ import annotations

from pathlib import Path

import pytest
from app.scenarios.loader import (
    load_scenario,
    validate_catalogue_alignment,
)
from app.scenarios.models import (
    ScenarioConfig,
    ScenarioConversation,
    ScenarioTurn,
)
from pydantic import ValidationError


def test_scenario_model_validation():
    # Valid model
    cfg = ScenarioConfig(
        name="test_scenario",
        description="A test scenario",
        concurrency=2,
        strategy="manual",
        target_endpoint_type="app_runs",
        conversations=[
            ScenarioConversation(
                conversation_id_prefix="c1",
                turns=[
                    ScenarioTurn(
                        question="Which pickup zones have the most trips?",
                        expected_tool="query_taxi_data",
                    )
                ],
            )
        ],
    )
    assert cfg.name == "test_scenario"
    assert cfg.total_conversations == 1
    assert cfg.total_turns == 1

    # Invalid empty conversations
    with pytest.raises(ValidationError):
        ScenarioConfig(
            name="empty_scenario",
            description="Empty",
            conversations=[],
        )

    # Invalid empty turns
    with pytest.raises(ValidationError):
        ScenarioConversation(conversation_id_prefix="c1", turns=[])

    # Invalid empty question
    with pytest.raises(ValidationError):
        ScenarioTurn(question="")


def test_load_all_canned_scenarios():
    scenarios_dir = Path("config/scenarios")
    expected = [
        ("shared_prefix_fanout", 20, 20),
        ("growing_multi_turn", 1, 5),
        ("strategy_comparison", 1, 3),
        ("concurrent_contention", 4, 12),
        ("e3_routing_mixed", 10, 39),
        ("e3_routing_large_prefix", 10, 39),
        ("e4_admission_overload", 22, 48),
    ]

    for name, expected_convs, expected_turns in expected:
        cfg = load_scenario(name, scenarios_dir=scenarios_dir)
        assert cfg.name == name
        assert cfg.total_conversations == expected_convs
        assert cfg.total_turns == expected_turns

        # Check all questions match query catalogue
        warnings = validate_catalogue_alignment(cfg)
        assert (
            warnings == []
        ), f"Scenario {name} had catalogue alignment warnings: {warnings}"


def test_catalogue_alignment_warnings():
    cfg = ScenarioConfig(
        name="misaligned",
        description="Has invalid question",
        conversations=[
            ScenarioConversation(
                turns=[
                    ScenarioTurn(question="What is the weather in Tokyo tomorrow?"),
                    ScenarioTurn(
                        question="Which pickup zones have the most trips?",
                        expected_tool="wrong_tool_name",
                    ),
                ]
            )
        ],
    )
    warnings = validate_catalogue_alignment(cfg)
    assert len(warnings) == 2
    assert "misses query catalogue" in warnings[0]
    assert "tool mismatch" in warnings[1]


def test_load_scenario_not_found():
    with pytest.raises(FileNotFoundError):
        load_scenario("non_existent_scenario_xyz")


def test_e3_e4_scenarios_are_gateway_scenarios_with_documented_tenants():
    e3 = load_scenario("e3_routing_mixed", scenarios_dir=Path("config/scenarios"))
    assert e3.target_endpoint_type == "gateway_chat" and e3.system_prefix
    assert all(3 <= len(c.turns) <= 5 for c in e3.conversations)
    assert (
        e3.total_conversations >= 8 and e3.policy_override is None
    )  # set per run by the CLI

    e4 = load_scenario("e4_admission_overload", scenarios_dir=Path("config/scenarios"))
    assert e4.target_endpoint_type == "gateway_chat" and e4.admission_mode is None
    tenants = {c.tenant_id for c in e4.conversations}
    assert tenants == {"tenant_interactive", "tenant_noisy", "tenant_batch"}
    by_tenant = {t: [c for c in e4.conversations if c.tenant_id == t] for t in tenants}
    assert len(by_tenant["tenant_noisy"]) > len(by_tenant["tenant_interactive"])
    assert all(c.workload_class == "batch" for c in by_tenant["tenant_batch"])
    tight = max(t.deadline_ms for c in by_tenant["tenant_interactive"] for t in c.turns)
    loose = min(t.deadline_ms for c in by_tenant["tenant_batch"] for t in c.turns)
    assert tight < loose


def _overlap_by_turn(name: str) -> list[float]:
    """Worst-case overlap per turn (gateway chars/4 estimate; every reply hits max_tokens):
    system-prefix tokens / current prefill tokens, the quantity the 0.8 gate compares.
    """
    import json

    cfg = load_scenario(name, scenarios_dir=Path("config/scenarios"))
    ptok = len(json.dumps([{"role": "system", "content": cfg.system_prefix}])) // 4
    q = max(len(t.question) for c in cfg.conversations for t in c.turns) // 4 + 8
    turns = max(len(c.turns) for c in cfg.conversations)
    return [
        ptok / (ptok + n * q + (n - 1) * cfg.max_tokens) for n in range(1, turns + 1)
    ]


def test_e3_headline_overlap_transition_is_not_tuned_to_the_gate():
    """Representative (small) prefix: affinity on turn 1, spill to load-based from turn 2.

    That transition is the E3 finding; the prefix must not be padded to hide it."""
    ov = _overlap_by_turn("e3_routing_mixed")
    assert ov[0] >= 0.8  # turn 1 sticky (prefix known from earlier conversations)
    assert all(o < 0.8 for o in ov[1:]), ov  # turns 2..5 spill (prefix_overlap_low)


def test_e3_headline_prefix_is_the_apps_canonical_prefix():
    """Drift guard: the scenario prefix is exactly the app's global prompt prefix, unpadded."""
    from app.benchmarks.canonical_prefix import canonical_system_prefix

    cfg = load_scenario("e3_routing_mixed", scenarios_dir=Path("config/scenarios"))
    assert cfg.system_prefix == canonical_system_prefix()
    assert all(c.system_prefix is None for c in cfg.conversations)
    assert (
        cfg.system_prefix.count("Dataset constraints:") == 1
    )  # used once, not repeated


def test_e3_large_prefix_is_explicitly_synthetic_and_keeps_affinity():
    cfg = load_scenario(
        "e3_routing_large_prefix", scenarios_dir=Path("config/scenarios")
    )
    assert "SYNTHETIC" in cfg.description and "e3_routing_mixed" in cfg.description
    assert all(o >= 0.8 for o in _overlap_by_turn("e3_routing_large_prefix"))
    headline = load_scenario("e3_routing_mixed", scenarios_dir=Path("config/scenarios"))
    assert (
        cfg.conversations == headline.conversations
    )  # same trace, only the prefix differs


def test_gateway_scenarios_fit_worker_context_window():
    """Worst case per turn (all replies hit max_tokens) must fit --max-model-len 8192.

    Uses the gateway's chars/4 estimate, which is approximate, hence the margin."""
    import json

    window, margin = 8192, 256
    for name in (
        "e3_routing_mixed",
        "e3_routing_large_prefix",
        "e4_admission_overload",
    ):
        cfg = load_scenario(name, scenarios_dir=Path("config/scenarios"))
        for conv in cfg.conversations:
            prefix = conv.system_prefix or cfg.system_prefix
            ptok = len(json.dumps([{"role": "system", "content": prefix}])) // 4
            prior = 0
            for turn in conv.turns:
                q = len(turn.question) // 4 + 8
                worst = ptok + prior + q + cfg.max_tokens
                assert worst <= window - margin, (
                    name,
                    conv.conversation_id_prefix,
                    worst,
                )
                prior += q + cfg.max_tokens


def test_max_tokens_default_and_bounds():
    base = dict(
        name="n",
        description="d",
        conversations=[ScenarioConversation(turns=[ScenarioTurn(question="q")])],
    )
    assert ScenarioConfig(**base).max_tokens == 512
    with pytest.raises(ValidationError):
        ScenarioConfig(**base, max_tokens=0)

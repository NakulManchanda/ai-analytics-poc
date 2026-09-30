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

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

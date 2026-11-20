"""#179: offline deterministic CI prompt-regression gate (zero paid model calls).

Validates the actual repo prompt renderer (`taxi_proposal_prompt` / `PLANNER_SINGLE_TURN`)
against behavioral and safety invariants, and proves that intentional prompt regressions
fail the gate deterministically without calling Bedrock or LiteLLM.
"""

from __future__ import annotations

import pytest
from app.llm import taxi_proposal_prompt
from app.prompts import PLANNER_SINGLE_TURN

SAMPLE_SCHEMA = {"columns": ["pickup_borough", "fare_amount", "trip_distance"]}
SAMPLE_QUESTION = "What is the average fare by pickup borough?"

# Phrases that must never leak from holdout cases into the planner prompt.
HOLDOUT_LEAK_PHRASES = (
    "February 2024",
    "January 2023",
    "January 15",
    "Which driver",
)


def evaluate_planner_prompt_contract(rendered: str) -> list[str]:
    """Deterministic contract checker for rendered planner-single-turn prompts.

    Returns a list of violation descriptions; an empty list passes the gate.
    """
    violations: list[str] = []
    if "Choose exactly ONE governed analysis" not in rendered:
        violations.append("missing single-tool constraint")
    if "Do not make multiple tool calls" not in rendered:
        violations.append("missing multi-tool prohibition")
    if "only reads data and cannot change it" not in rendered:
        violations.append("missing read-only decline clause")
    if "dataset does not contain that information" not in rendered:
        violations.append("missing unsupported-scope decline clause")
    if "dataset must use a tool" not in rendered:
        violations.append("missing positive tool-use anchor")
    if "no tool" in rendered.lower():
        violations.append("forbidden imperative 'no tool' phrase leaks into answers")
    for leak in HOLDOUT_LEAK_PHRASES:
        if leak.lower() in rendered.lower():
            violations.append(f"holdout leakage detected: {leak!r}")
    return violations


def test_current_planner_prompt_passes_offline_regression_gate() -> None:
    rendered = taxi_proposal_prompt(SAMPLE_QUESTION, SAMPLE_SCHEMA)
    assert PLANNER_SINGLE_TURN.version == "1.2.0"
    assert evaluate_planner_prompt_contract(rendered) == []


@pytest.mark.parametrize(
    ("name", "mutated", "expected_fragment"),
    [
        (
            "dropped_positive_anchor",
            lambda s: s.replace(
                "A question about trip counts, fares, distances, tips, zones, "
                "boroughs, payment types or times in the dataset must use a tool.",
                "",
            ),
            "missing positive tool-use anchor",
        ),
        (
            "leaked_imperative_no_tool",
            lambda s: s.replace("do not use a tool", "call no tool"),
            "forbidden imperative 'no tool'",
        ),
        (
            "dropped_read_only_decline",
            lambda s: s.replace(
                "only reads data and cannot change it",
                "handles taxi queries",
            ),
            "missing read-only decline clause",
        ),
        (
            "dropped_single_tool_rule",
            lambda s: s.replace("Do not make multiple tool calls.", ""),
            "missing multi-tool prohibition",
        ),
        (
            "holdout_case_leaked",
            lambda s: s
            + "\nExample: Predict how many trips there will be in February 2024.",
            "holdout leakage detected",
        ),
    ],
)
def test_intentional_prompt_regressions_fail_offline_gate(
    name: str, mutated, expected_fragment: str
) -> None:
    """Proves the offline CI gate rejects each known regression without paid calls."""
    baseline = taxi_proposal_prompt(SAMPLE_QUESTION, SAMPLE_SCHEMA)
    regressed = mutated(baseline)
    violations = evaluate_planner_prompt_contract(regressed)
    assert any(
        expected_fragment in v for v in violations
    ), f"regression {name!r} was not caught; got violations={violations!r}"

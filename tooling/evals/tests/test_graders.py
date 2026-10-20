import pytest

from evals.graders import (
    args_match,
    extract_numbers,
    fabricated_numbers,
    grade_fact,
    grade_turn,
    number_matches,
    tool_score,
)
from evals.schema import ExpectedTool, Fact, Turn
from evals.traces import ToolUse, TurnView


def test_extract_numbers_handles_formats():
    assert extract_numbers("1,234 trips, $18.20, 23.8% and 2.5 million") == [
        1234,
        18.2,
        23.8,
        2_500_000,
    ]


def test_number_match_tolerance_and_percent():
    assert number_matches(145240, "about 145,240 trips", 0.001)
    assert not number_matches(145240, "about 150,000 trips", 0.001)
    assert number_matches(0.2376, "tip rate is 23.8%", 0.01)


def test_text_fact_case_insensitive():
    f = Fact(label="z", kind="text", col="c", value="JFK Airport")
    assert grade_fact(f, "the top zone is jfk airport")
    assert not grade_fact(f, "LaGuardia")


def test_fact_without_value_is_error():
    with pytest.raises(ValueError):
        grade_fact(Fact(label="z", kind="number", col="c"), "1")


def test_fabricated_numbers_ignores_years_and_small_ints():
    assert fabricated_numbers("In 2023 there were 3 reasons") == []
    assert fabricated_numbers("about 120,000 trips") == [120000]
    assert fabricated_numbers("about 4.5 percent") == [4.5]


def test_args_subset_ignores_extras_and_list_order():
    exp = ExpectedTool(name="t", arguments={"dimensions": ["a", "b"]})
    assert args_match(exp, {"dimensions": ["b", "a"], "limit": 20})
    assert not args_match(exp, {"dimensions": ["a"]})
    assert not args_match(
        ExpectedTool(name="t", arguments={"x": 1}, match="exact"), {"x": 1, "y": 2}
    )


def test_tool_score_partial_and_no_expected():
    exp = [ExpectedTool(name="a"), ExpectedTool(name="b")]
    assert tool_score(exp, [ToolUse("a", {}, "ok", "q", 1)]) == 0.5
    assert tool_score([], []) == 1.0
    assert tool_score([], [ToolUse("a", {}, "ok", "q", 1)]) == 0.0


def _view(answer, tools=(), **kw):
    v = TurnView("r", "c", 0, answer=answer)
    v.tools = list(tools)
    return v


def test_grade_turn_answer_pass_and_fail():
    turn = Turn(
        question="q",
        expected_tools=[ExpectedTool(name="a")],
        max_steps=1,
        reference={
            "sql": "s",
            "facts": [Fact(label="n", kind="number", col="c", value=10.0)],
        },
    )
    ok = grade_turn("c", 0, turn, _view("it is 10", [ToolUse("a", {}, "ok", "q1", 1)]))
    assert ok.passed
    wrong = grade_turn(
        "c", 0, turn, _view("it is 99", [ToolUse("a", {}, "ok", "q1", 1)])
    )
    assert not wrong.passed and not wrong.checks["numeric_facts"]
    noevidence = grade_turn(
        "c", 0, turn, _view("it is 10", [ToolUse("a", {}, "error", None, None)])
    )
    assert not noevidence.checks["evidence_present"]
    assert grade_turn("c", 0, turn, None).missing


def test_grade_turn_unsupported_requires_limitation_and_no_numbers():
    turn = Turn(question="q", behavior="unsupported", must_mention_any=["cannot"])
    assert grade_turn("c", 0, turn, _view("I cannot tell.")).passed
    assert not grade_turn("c", 0, turn, _view("I cannot tell, maybe 5,000.")).passed
    assert not grade_turn("c", 0, turn, _view("Sure!")).passed
    assert not grade_turn(
        "c", 0, turn, _view("I cannot", [ToolUse("a", {}, "ok", "q", 1)])
    ).passed


def _multi001_turn2():
    from evals.dataset import DEFAULT_DATASET, load_cases

    case = next(c for c in load_cases(DEFAULT_DATASET) if c.id == "multi-001")
    return case.turns[1]


def _compare(base, comp):
    return ToolUse(
        "compare_taxi_segments",
        {
            "segment_dimension": "pickup_zone",
            "measures": ["trip_count"],
            "baseline_filters": {"pickup_zone": base, "extra": 1},
            "comparison_filters": {"pickup_zone": comp},
            "limit": 5,
        },
        "ok",
        "q",
        1,
    )


def test_nested_subset_matches_entities_and_ignores_extra_nested_keys():
    exp = _multi001_turn2().expected_tools
    assert tool_score(exp, [_compare("JFK Airport", "Midtown Center")]) == 1.0
    assert tool_score(exp, [_compare("Midtown Center", "JFK Airport")]) == 0.0


def _grade(*tools):
    turn = _multi001_turn2()
    return grade_turn(
        "multi-001",
        1,
        turn,
        TurnView("r", "multi-001", 1, answer="x", tools=list(tools)),
    )


def test_multi001_turn2_fails_tool_arguments_for_wrong_zones_or_unrelated_tool():
    assert _grade(_compare("JFK Airport", "Midtown Center")).checks["tool_arguments"]
    assert not _grade(_compare("Chelsea", "SoHo")).checks["tool_arguments"]
    # the real failed live turn: average_trip_metrics called with {}
    live_failure = ToolUse("average_trip_metrics", {}, "ok", "q", 1)
    assert not _grade(live_failure).checks["tool_arguments"]

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


# --- grade_turn_v2: three outcomes from tool output (issue #188) -------------------------
from evals.graders import TurnOutcome, grade_turn_v2  # noqa: E402
from evals.traces import ToolOutput  # noqa: E402


def _v2_turn(
    question="q",
    behavior="answer",
    tools=None,
    facts=None,
    must=None,
    max_steps=3,
):
    return Turn(
        question=question,
        behavior=behavior,
        expected_tools=tools or [],
        max_steps=max_steps,
        reference={"facts": facts or []},
        must_mention_any=must or [],
    )


def _num_fact(label, value, rel_tol=0.001):
    return Fact(label=label, kind="number", col="c", value=value, rel_tol=rel_tol)


def _view(answer, tool_name, args, columns, rows, truncated=False, question="q"):
    out = ToolOutput(columns, rows, truncated, len(rows), "query_1")
    use = ToolUse(tool_name, args, "completed", "query_1", len(rows), output=out)
    return TurnView("run_1", "c", 0, question=question, answer=answer, tools=[use])


def _grade(turn, view, **kw):
    kw.setdefault("tool_output_available", True)
    return grade_turn_v2("c", 0, turn, view, **kw)


def test_v2_clean_answer_is_correct():
    turn = _v2_turn(
        question="Which zone has the most trips?",
        tools=[ExpectedTool(name="q", arguments={"limit": 1})],
        facts=[Fact(label="top_zone", kind="text", col="z", value="JFK Airport"),
               _num_fact("top_zone_trips", 145240)],
    )
    view = _view(
        "JFK Airport has the most trips: 145,240.",
        "q",
        {"limit": 1},
        ["pickup_zone", "trip_count"],
        [["JFK Airport", 145240]],
        truncated=True,
        question=turn.question,
    )
    res = _grade(turn, view)
    assert isinstance(res, TurnOutcome)
    assert (res.execution, res.tool, res.answer) == ("correct", "correct", "correct")


def test_det006_truncated_result_presented_as_complete():
    turn = _v2_turn(
        question="Compare weekday and weekend trip volume by hour",
        tools=[ExpectedTool(name="agg", arguments={"measures": ["trip_count"]})],
        facts=[_num_fact("top_trips", 162761)],
    )
    rows = [["weekday", h, 1000 + h] for h in range(19)] + [["weekday", 18, 162761]]
    view = _view(
        "Weekday 18: 162,761 trips. Without weekend data we cannot compare.",
        "agg",
        {"measures": ["trip_count"], "limit": 20},
        ["pickup_week_part", "pickup_hour", "trip_count"],
        rows,
        truncated=True,
        question=turn.question,
    )
    res = _grade(turn, view)
    assert res.answer == "incorrect"
    assert any("truncat" in r for r in res.reasons)


def test_truncated_ranking_question_is_not_flagged():
    turn = _v2_turn(
        question="What are the busiest pickup hours?",
        facts=[_num_fact("peak_hour_trips", 212788)],
    )
    view = _view(
        "The busiest hour has 212,788 trips.",
        "q",
        {"limit": 1},
        ["hour", "trip_count"],
        [[18, 212788]],
        truncated=True,
        question=turn.question,
    )
    assert _grade(turn, view).answer == "correct"


def test_det009_false_comparative_claim():
    turn = _v2_turn(
        question="Compare average trip distance across boroughs",
        facts=[_num_fact("busiest_borough_avg_distance", 2.67)],
    )
    view = _view(
        "Manhattan: 2.67 miles. Queens: 12.39 miles. Brooklyn: 13.24 miles.\n\n"
        "Queens has the longest average trip distance, while Manhattan has the "
        "shortest average trip distance.",
        "avg",
        {},
        ["region_name", "average_trip_distance"],
        [["Manhattan", 2.67], ["Queens", 12.39], ["Brooklyn", 13.24]],
        question=turn.question,
    )
    res = _grade(turn, view)
    assert res.answer == "incorrect"
    assert any("comparative" in r for r in res.reasons)


def test_true_comparative_claim_passes():
    turn = _v2_turn(
        question="Compare average trip distance across boroughs",
        facts=[_num_fact("busiest_borough_avg_distance", 2.67)],
    )
    view = _view(
        "Manhattan: 2.67 miles. Brooklyn: 13.24 miles. Brooklyn has the longest "
        "average trip distance.",
        "avg",
        {},
        ["region_name", "average_trip_distance"],
        [["Manhattan", 2.67], ["Brooklyn", 13.24]],
        question=turn.question,
    )
    assert _grade(turn, view).answer == "correct"


_PAY_ROWS = [[0, 138048], [1, 2318999], [2, 430859]]
_PAY_COLS = ["payment_type", "trip_count"]
_PAY_FACTS = [_num_fact("credit_card_trips", 2318999), _num_fact("cash_trips", 430859)]


def test_det010_value_attached_to_wrong_label():
    turn = _v2_turn(question="credit card vs cash", facts=_PAY_FACTS)
    answer = (
        "- **Credit Card Payment (payment_type = 1):**\n"
        "  - **Trip Count:** 2,318,999\n\n"
        "- **Cash Payment (payment_type = 0):**\n"
        "  - **Trip Count:** 138,048\n\n"
        "- **Other Payment Types (payment_type = 2):**\n"
        "  - **Trip Count:** 430,859\n"
    )
    res = _grade(turn, _view(answer, "agg", {}, _PAY_COLS, _PAY_ROWS))
    assert res.answer == "incorrect"
    assert any("association" in r and "cash" in r for r in res.reasons)


def test_value_with_its_label_in_same_clause_passes():
    turn = _v2_turn(question="credit card vs cash", facts=_PAY_FACTS)
    answer = "Credit card had 2,318,999 trips, and cash had 430,859 trips."
    assert _grade(turn, _view(answer, "agg", {}, _PAY_COLS, _PAY_ROWS)).answer == "correct"


def test_label_never_mentioned_is_not_flagged_by_association():
    turn = _v2_turn(question="credit card vs cash", facts=_PAY_FACTS)
    answer = "Payment type 1 had 2,318,999 trips; type 2 had 430,859 trips."
    res = _grade(turn, _view(answer, "agg", {}, _PAY_COLS, _PAY_ROWS))
    assert res.answer != "incorrect"


def test_det007_forbidden_segment_dimension_makes_tool_incorrect():
    exp = ExpectedTool(
        name="cmp",
        arguments={"measures": ["average_fare"]},
        forbidden_args=["segment_dimension"],
    )
    turn = _v2_turn(tools=[exp], facts=[_num_fact("airport_avg_fare", 55.99)])
    view = _view(
        "x", "cmp", {"measures": ["average_fare"], "segment_dimension": "payment_type"},
        ["payment_type"], [[1]],
    )
    res = _grade(turn, view)
    assert res.tool == "incorrect"
    assert any("segment_dimension" in r for r in res.reasons)


def test_det013_required_arg_missing_makes_tool_incorrect():
    exp = ExpectedTool(
        name="cmp", arguments={"measures": ["trip_count"]}, required_args=["segment_dimension"]
    )
    turn = _v2_turn(tools=[exp], facts=[_num_fact("v", 5)])
    view = _view("5", "cmp", {"measures": ["trip_count"]}, ["c"], [[5]])
    res = _grade(turn, view)
    assert res.tool == "incorrect"
    assert any("required" in r for r in res.reasons)


def test_multi004_equivalent_legacy_tool_is_needs_review_with_correct_answer():
    turn = _v2_turn(
        question="What is the average fare for Manhattan pickups?",
        tools=[ExpectedTool(name="aggregate_taxi_data", arguments={"measures": ["average_fare"]})],
        facts=[_num_fact("manhattan_avg_fare", 15.05)],
    )
    view = _view(
        "The average fare for Manhattan pickups is $15.05.",
        "average_trip_metrics",
        {"region_name": "Manhattan"},
        ["region_name", "trip_count", "average_fare_amount"],
        [["Manhattan", 2616430, 15.05]],
        question=turn.question,
    )
    res = _grade(turn, view)
    assert (res.tool, res.answer) == ("needs_review", "correct")
    assert any("equivalen" in r for r in res.reasons)


def test_different_tool_without_matching_value_is_incorrect():
    turn = _v2_turn(
        tools=[ExpectedTool(name="aggregate_taxi_data")],
        facts=[_num_fact("airport_distance", 13.51)],
    )
    view = _view("It is 15.05.", "average_trip_metrics", {}, ["f"], [[15.05]])
    res = _grade(turn, view)
    assert res.tool == "incorrect" and res.answer == "incorrect"


_UNS = dict(behavior="unsupported", must=["does not contain", "cannot"])


def test_uns003_numbers_present_in_tool_output_are_not_fabricated():
    turn = _v2_turn(question="Which driver earned the most money?", **_UNS)
    view = _view(
        "The result does not contain driver earnings. Manhattan had 2,616,430 trips "
        "and a 15.05 average fare.",
        "average_trip_metrics",
        {"region_name": "Manhattan"},
        ["region_name", "trip_count", "average_fare_amount"],
        [["Manhattan", 2616430, 15.05]],
        question=turn.question,
    )
    res = _grade(turn, view)
    assert res.answer == "correct"
    assert res.tool == "incorrect"  # unneeded call


def test_unsupported_numbers_not_in_output_are_fabricated():
    turn = _v2_turn(question="Forecast next month", **_UNS)
    view = _view(
        "I cannot be sure, but expect about 3,100,000 trips.",
        "q",
        {},
        ["trip_count"],
        [[2964621]],
        question=turn.question,
    )
    res = _grade(turn, view)
    assert res.answer == "incorrect"
    assert any("fabricat" in r or "not in tool output" in r for r in res.reasons)


def test_uns004_unneeded_call_tool_incorrect_answer_judged_alone():
    turn = _v2_turn(question="How did the weather affect tipping?", **_UNS)
    view = _view(
        "The result does not contain weather. I cannot answer this.",
        "average_trip_metrics",
        {},
        ["region_name"],
        [["Manhattan"]],
        question=turn.question,
    )
    res = _grade(turn, view)
    assert (res.tool, res.answer) == ("incorrect", "correct")


def test_unsupported_with_no_tool_call_is_correct():
    turn = _v2_turn(**_UNS)
    view = TurnView("r", "c", 0, answer="I cannot do that.", tools=[])
    res = _grade(turn, view)
    assert (res.tool, res.answer) == ("correct", "correct")


def test_fault_injection_cases_are_not_applicable():
    turn = _v2_turn(behavior="failure", must=["error"])
    view = TurnView("r", "c", 0, answer="Zone numbers: 145,240", tools=[])
    res = _grade(turn, view, tags=("fault-injection",))
    assert (res.execution, res.tool, res.answer) == ("not_applicable",) * 3


def test_missing_tool_output_never_passes():
    turn = _v2_turn(facts=[_num_fact("x_trips", 100)])
    use = ToolUse("q", {}, "completed", "query_1", 1)  # output unknown
    view = TurnView("r", "c", 0, answer="There were 100 trips.", tools=[use])
    res = _grade(turn, view, tool_output_available=False)
    assert res.answer == "needs_review"
    assert any("tool output unavailable" in r for r in res.reasons)


def test_missing_view_is_execution_incorrect():
    res = _grade(_v2_turn(), None)
    assert res.execution == "incorrect"
    assert res.tool == res.answer == "not_applicable"

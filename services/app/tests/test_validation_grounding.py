from __future__ import annotations

from app.validation import check_answer_grounding

EVIDENCE = {
    "columns": ["pickup_zone", "trip_count", "average_fare"],
    "rows": [["JFK Airport", 15000, 52.3], ["LaGuardia Airport", 12000, 38.75]],
    "row_count": 2,
    "execution_duration_ms": 12,
    "query_id": "fake-query-123",
    "truncated": False,
}


def test_grounded_answer_passes() -> None:
    report = check_answer_grounding(
        "JFK Airport leads with 15,000 trips at an average fare of $52.30; "
        "LaGuardia Airport has 12,000 trips (query id fake-query-123).",
        EVIDENCE,
    )
    assert report.ok
    assert report.caveat() is None


def test_fabricated_number_is_flagged() -> None:
    report = check_answer_grounding("JFK Airport had 98,765 trips.", EVIDENCE)
    assert not report.ok
    assert 98765.0 in report.ungrounded_numbers
    assert "98765" in (report.caveat() or "").replace(",", "")


def test_fabricated_percent_and_money_are_flagged_even_when_small() -> None:
    assert not check_answer_grounding("Fares rose 12% overall.", EVIDENCE).ok
    assert not check_answer_grounding("The fare was $9.99.", EVIDENCE).ok


def test_derived_values_from_evidence_are_grounded() -> None:
    # difference 3000, ratio share 15000/27000 = 55.6%, sum 27000
    report = check_answer_grounding(
        "JFK has 3,000 more trips, 55.6% of the 27,000 combined trips.", EVIDENCE
    )
    assert report.ok, report.ungrounded_numbers


def test_small_ordinals_years_and_question_numbers_are_ignored() -> None:
    report = check_answer_grounding(
        "Top 2 zones in 2024: hour 17 is busiest; 1,500 was your threshold.",
        EVIDENCE,
        question="Which zones beat 1,500 trips?",
    )
    assert report.ok, report.ungrounded_numbers


def test_scaled_numbers_and_rounding() -> None:
    assert check_answer_grounding("About 15k trips, avg fare $52.3.", EVIDENCE).ok
    assert check_answer_grounding("Roughly 0.015 million trips.", EVIDENCE).ok
    assert not check_answer_grounding("About 16k trips.", EVIDENCE).ok


def test_wrong_query_id_citation_is_flagged() -> None:
    report = check_answer_grounding(
        "Result from query_id: q-made-up-999 shows JFK on top.", EVIDENCE
    )
    assert not report.ok
    assert report.unknown_query_ids == ["q-made-up-999"]


def test_unsupported_causal_claims_are_flagged_but_hedges_pass() -> None:
    caused = check_answer_grounding(
        "JFK has more trips because of the holiday rush.", EVIDENCE
    )
    assert caused.causal_claims
    assert caused.ok  # causal claims are flagged, not numeric-blocking
    assert "causal" in (caused.caveat() or "").lower()
    hedged = check_answer_grounding(
        "JFK has more trips; the data cannot say whether this is due to travel season.",
        EVIDENCE,
    )
    assert hedged.causal_claims == []


def test_empty_or_missing_evidence_means_any_big_number_is_ungrounded() -> None:
    report = check_answer_grounding("There were 5,000 trips.", None)
    assert not report.ok

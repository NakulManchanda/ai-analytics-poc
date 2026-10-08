"""The serve smoke must fail when a stage header is missing or a reject is not stamped."""

from app.benchmarks.serve_smoke import (
    missing_stage_headers,
    problems_for_accepted,
    problems_for_rejected,
)

OK = {
    "x-guard-decision": "allow",
    "x-admit-decision": "accept",
    "x-place-decision": "worker_a",
    "x-queue-decision": "dispatched",
}


def test_complete_accepted_request_has_no_problems() -> None:
    assert problems_for_accepted(OK) == []


def test_header_names_are_case_insensitive() -> None:
    assert problems_for_accepted({k.title(): v for k, v in OK.items()}) == []


def test_each_missing_stage_header_is_reported() -> None:
    for name in OK:
        headers = {k: v for k, v in OK.items() if k != name}
        assert missing_stage_headers(headers) == [name]
        assert f"missing {name}" in problems_for_accepted(headers)


def test_empty_header_value_counts_as_missing() -> None:
    assert missing_stage_headers({**OK, "x-place-decision": "  "}) == [
        "x-place-decision"
    ]


def test_wrong_decisions_are_reported() -> None:
    problems = problems_for_accepted(
        {
            **OK,
            "x-guard-decision": "reject:prompt_too_long",
            "x-queue-decision": "timeout_queue",
        }
    )
    assert any("x-guard-decision" in p for p in problems)
    assert any("x-queue-decision" in p for p in problems)


def test_guard_reject_is_recognised() -> None:
    headers = {
        "x-guard-decision": "reject:prompt_too_long",
        "x-admit-decision": "not_evaluated",
    }
    assert (
        problems_for_rejected(
            413, headers, expected_status=413, decision_prefix="reject:"
        )
        == []
    )


def test_reject_with_wrong_status_or_no_stamp_is_reported() -> None:
    headers = {"x-guard-decision": "allow", "x-admit-decision": "accept"}
    problems = problems_for_rejected(
        200, headers, expected_status=413, decision_prefix="reject:"
    )
    assert len(problems) == 2


def test_admission_shed_is_recognised_by_prefix() -> None:
    headers = {"x-guard-decision": "allow", "x-admit-decision": "shed:decode_slots"}
    assert (
        problems_for_rejected(
            503, headers, expected_status=503, decision_prefix="shed:"
        )
        == []
    )

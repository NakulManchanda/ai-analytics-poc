from __future__ import annotations

from app.query_catalogue import lookup, normalize_question


def test_normalize_question_collapses_case_and_whitespace() -> None:
    assert normalize_question("  Which   Pickup Zones\thave the most trips?  ") == (
        "which pickup zones have the most trips?"
    )


def test_catalogue_hit_for_a_ui_chip_question() -> None:
    hit = lookup("Which pickup zones have the most trips?")
    assert hit is not None
    tool_name, arguments = hit
    assert tool_name == "query_taxi_data"
    assert arguments == {"analysis": "top_pickup_zones", "limit": 5}


def test_catalogue_hit_is_case_and_whitespace_insensitive() -> None:
    hit = lookup("  which PICKUP zones   have the most trips?")
    assert hit is not None


def test_dataset_chip_maps_to_describe_taxi_dataset() -> None:
    hit = lookup(
        "What date range, row count, and columns does this taxi dataset cover?"
    )
    assert hit == ("describe_taxi_dataset", {"include_column_stats": False})


def test_followup_question_is_not_in_catalogue() -> None:
    # D19: follow-ups must fall through to the model, which now has real
    # prior-turn context via the renderer, not a catalogue shortcut.
    assert lookup("Compare that with the second highest zone") is None
    assert lookup("what about last month") is None

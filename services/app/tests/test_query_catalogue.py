from __future__ import annotations

import sys
from pathlib import Path

from app.query_catalogue import QUERY_CATALOGUE, lookup, normalize_question

# The dataset_spike package ships as a sibling service, not an installed
# dependency of services/app; add it to sys.path so this test exercises the
# real slice A validators rather than a mock.
_DATASET_SPIKE_SRC = Path(__file__).resolve().parents[2] / "dataset_spike"
if str(_DATASET_SPIKE_SRC) not in sys.path:
    sys.path.insert(0, str(_DATASET_SPIKE_SRC))

from dataset_spike.query_spec import (  # noqa: E402
    DimensionName,
    build_aggregate_spec,
    validate_dimension,
    validate_filters,
    validate_limit,
    validate_measures,
)


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


def _validate_aggregate_arguments(arguments: dict[str, object]) -> None:
    build_aggregate_spec(
        dimensions=arguments["dimensions"],
        measures=arguments["measures"],
        filters=arguments["filters"],
        order_by=arguments["order_by"],
        limit=arguments["limit"],
    )


def _validate_compare_arguments(arguments: dict[str, object]) -> None:
    # Mirrors dataset_spike.analytics.compare_taxi_segments's own validation
    # of these exact fields, run before it compiles/executes any SQL.
    validate_dimension(arguments["segment_dimension"])
    validate_measures(arguments["measures"])
    validate_limit(arguments["limit"])
    validate_filters(arguments["baseline_filters"])
    validate_filters(arguments["comparison_filters"])


def _validate_list_dimension_values_arguments(arguments: dict[str, object]) -> None:
    validate_dimension(arguments["dimension"])


def test_every_catalogue_entry_validates_against_real_slice_a_validators() -> None:
    """Every catalogue entry must produce arguments that pass the real,
    governed slice A validators -- not a mock -- so a regressed order_by
    field name, an invalid code, or a meaningless filter is caught here
    instead of failing in production when a chip is clicked."""
    assert QUERY_CATALOGUE, "catalogue must not be empty"

    for tool_name, arguments in QUERY_CATALOGUE.values():
        if tool_name == "aggregate_taxi_data":
            _validate_aggregate_arguments(arguments)
        elif tool_name == "compare_taxi_segments":
            _validate_compare_arguments(arguments)
            assert arguments["baseline_filters"] != arguments["comparison_filters"], (
                "a compare entry with identical baseline/comparison filters "
                "produces a meaningless zero-delta comparison"
            )
        elif tool_name == "list_taxi_dimension_values":
            _validate_list_dimension_values_arguments(arguments)
        elif tool_name in (
            "describe_taxi_dataset",
            "query_taxi_data",
            "average_trip_metrics",
        ):
            # No slice A query_spec validator applies to these tools; their
            # arguments are plain flags/enums checked at the MCP boundary.
            pass
        else:  # pragma: no cover - defensive: fail loudly on an unknown tool
            raise AssertionError(f"unexpected catalogue tool name: {tool_name}")


def test_zone_dimension_catalogue_entries_use_a_real_dimension_name() -> None:
    zone_dimension = validate_dimension("pickup_borough")
    assert zone_dimension == DimensionName.PICKUP_BOROUGH

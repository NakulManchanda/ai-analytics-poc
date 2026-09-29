"""Fixed catalogue of pre-canned questions -> governed tool call.

Covers the 16 sample-question chips shipped in ``web/src/App.tsx`` (plus the
Dataset chips) so a catalogue hit skips the model tool-selection call
entirely (D19): the run is replayable and a model proposal error can never
break one of these known-good scenarios. A catalogue MISS (including any
follow-up question, e.g. "compare that with the second highest zone") falls
through to the normal model-based tool proposal, unchanged.

Keys are normalized (lowercased, whitespace-collapsed) exactly like the
lookup normalizer in ``orchestration/loop.py`` applies to the incoming
question, so entries here must be written in their natural-cased form and
normalized consistently via ``normalize_question``.
"""

from __future__ import annotations

import re

TOOL_NAME = str
ToolArguments = dict[str, object]


def normalize_question(question: str) -> str:
    """Lowercase and collapse whitespace, matching the catalogue's keys."""
    return re.sub(r"\s+", " ", question.strip().lower())


_RAW_CATALOGUE: dict[str, tuple[str, ToolArguments]] = {
    # Dataset chips
    "What date range, row count, and columns does this taxi dataset cover?": (
        "describe_taxi_dataset",
        {"include_column_stats": False},
    ),
    "What payment types, rate codes, and vendors appear in the data, and what do the codes mean?": (
        "describe_taxi_dataset",
        {"include_column_stats": True},
    ),
    "List the pickup boroughs present in the dataset": (
        "list_taxi_dimension_values",
        {"dimension": "pickup_borough", "search": None, "limit": 20},
    ),
    # Zones / volume chips
    "Which pickup zones have the most trips?": (
        "query_taxi_data",
        {"analysis": "top_pickup_zones", "limit": 5},
    ),
    "What are the peak hours and busiest times for taxi rides in NYC?": (
        "query_taxi_data",
        {"analysis": "trip_volume_by_hour", "limit": 20},
    ),
    "Compare weekday and weekend trip volume by hour": (
        "aggregate_taxi_data",
        {
            "dimensions": ["pickup_week_part", "pickup_hour"],
            "measures": ["trip_count"],
            "filters": None,
            "order_by": None,
            "limit": 20,
        },
    ),
    "How do airport trips compare with non-airport trips on fare and duration?": (
        "compare_taxi_segments",
        {
            "segment_dimension": "airport_trip",
            "measures": ["average_fare", "average_duration_minutes"],
            "baseline_filters": {"airport_trip": False},
            "comparison_filters": {"airport_trip": True},
            "limit": 20,
        },
    ),
    "Which pickup zone has the longest average trip duration?": (
        "aggregate_taxi_data",
        {
            "dimensions": ["pickup_zone"],
            "measures": ["average_duration_minutes"],
            "filters": None,
            "order_by": {"measure": "average_duration_minutes", "direction": "desc"},
            "limit": 5,
        },
    ),
    "Compare average trip distance and fare amount across major pickup boroughs": (
        "average_trip_metrics",
        {"region_name": None},
    ),
    # Payments chips
    "What is the breakdown of credit card vs cash payment types and average tips?": (
        "aggregate_taxi_data",
        {
            "dimensions": ["payment_type"],
            "measures": ["trip_count", "average_tip"],
            "filters": None,
            "order_by": None,
            "limit": 20,
        },
    ),
    "What is the average tip rate for credit card payments by pickup borough?": (
        "aggregate_taxi_data",
        {
            "dimensions": ["pickup_borough"],
            "measures": ["tip_rate"],
            "filters": {"payment_type": "credit_card"},
            "order_by": None,
            "limit": 20,
        },
    ),
    "Which fare amount bucket has the most trips, and what is the median trip distance in that bucket?": (
        "aggregate_taxi_data",
        {
            "dimensions": ["fare_amount_bucket"],
            "measures": ["trip_count", "median_distance"],
            "filters": None,
            "order_by": {"measure": "trip_count", "direction": "desc"},
            "limit": 20,
        },
    ),
    "What is the average fare and trip count by trip distance bucket?": (
        "aggregate_taxi_data",
        {
            "dimensions": ["trip_distance_bucket"],
            "measures": ["average_fare", "trip_count"],
            "filters": None,
            "order_by": None,
            "limit": 20,
        },
    ),
    # Comparisons chips
    "Compare Manhattan and Queens on trip count, average fare, and average tip": (
        "compare_taxi_segments",
        {
            "segment_dimension": "pickup_borough",
            "measures": ["trip_count", "average_fare", "average_tip"],
            "baseline_filters": {"pickup_borough": "Manhattan"},
            "comparison_filters": {"pickup_borough": "Queens"},
            "limit": 20,
        },
    ),
    "Compare morning rush hour (7-10am) and evening rush hour (4-7pm) trip duration by borough": (
        "compare_taxi_segments",
        {
            "segment_dimension": "pickup_borough",
            "measures": ["average_duration_minutes"],
            "baseline_filters": {"pickup_hour_min": 7, "pickup_hour_max": 10},
            "comparison_filters": {"pickup_hour_min": 16, "pickup_hour_max": 19},
            "limit": 20,
        },
    ),
    "Which borough contributed most to the busiest pickup hour, and which zones inside it dominate?": (
        "aggregate_taxi_data",
        {
            "dimensions": ["pickup_hour", "pickup_borough"],
            "measures": ["trip_count"],
            "filters": None,
            "order_by": {"measure": "trip_count", "direction": "desc"},
            "limit": 20,
        },
    ),
    "Compare the first and last complete weeks of the dataset on trip count and average fare": (
        "compare_taxi_segments",
        {
            "segment_dimension": "pickup_date",
            "measures": ["trip_count", "average_fare"],
            "baseline_filters": {},
            "comparison_filters": {},
            "limit": 20,
        },
    ),
    "Compare pickup and dropoff borough distributions": (
        "aggregate_taxi_data",
        {
            "dimensions": ["pickup_borough", "dropoff_borough"],
            "measures": ["trip_count"],
            "filters": None,
            "order_by": None,
            "limit": 20,
        },
    ),
}

# Normalized lookup: normalize_question(key) -> (tool_name, tool_arguments).
QUERY_CATALOGUE: dict[str, tuple[str, ToolArguments]] = {
    normalize_question(question): value for question, value in _RAW_CATALOGUE.items()
}


def lookup(question: str) -> tuple[str, ToolArguments] | None:
    """Return the catalogue's (tool_name, tool_arguments) for a normalized match,
    or None on a miss (including any follow-up question not in the catalogue)."""
    return QUERY_CATALOGUE.get(normalize_question(question))

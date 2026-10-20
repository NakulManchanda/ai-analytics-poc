"""Semantic validation of planner tool proposals, run BEFORE any governed tool executes.

The MCP server stays authoritative (it re-validates and authorizes every call);
this module is a cheap pre-flight that (a) keeps malformed or out-of-scope
proposals from ever crossing the MCP boundary and (b) produces *deterministic*
feedback so one recoverable mistake can be corrected without a blind retry.
The allowlists mirror ``dataset_spike.query_spec``; ``test_validation_proposal``
checks every catalogue entry still passes so drift is caught.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_ROWS = 64
MAX_LIST_LENGTH = 16
MAX_STRING_LENGTH = 128

ANALYSES = ("top_pickup_zones", "trip_volume_by_hour", "average_distance_by_weekday")
BOROUGHS = ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island")
WEEKDAYS = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)
WEEK_PARTS = frozenset({"weekday", "weekend"})
DIMENSIONS = frozenset(
    {
        "pickup_date",
        "pickup_hour",
        "pickup_weekday",
        "pickup_week_part",
        "pickup_zone",
        "pickup_borough",
        "dropoff_zone",
        "dropoff_borough",
        "payment_type",
        "rate_code",
        "vendor",
        "passenger_count_bucket",
        "trip_distance_bucket",
        "trip_duration_bucket",
        "fare_amount_bucket",
        "airport_trip",
    }
)
MEASURES = frozenset(
    {
        "trip_count",
        "trip_share",
        "average_fare",
        "median_fare",
        "average_tip",
        "median_tip",
        "total_tips",
        "tip_rate",
        "average_total_amount",
        "average_distance",
        "median_distance",
        "average_duration_minutes",
        "median_duration_minutes",
        "p95_duration_minutes",
        "average_passenger_count",
    }
)
PAYMENT_TYPE_CODES = frozenset(range(0, 7))
RATE_CODE_CODES = frozenset({1, 2, 3, 4, 5, 6, 99})
VENDOR_CODES = frozenset({1, 2, 6})

# filter name -> kind
_FILTER_KINDS: dict[str, str] = {
    "start_timestamp": "timestamp",
    "end_timestamp": "timestamp",
    "pickup_hour_min": "hour",
    "pickup_hour_max": "hour",
    "pickup_weekday": "weekday",
    "pickup_week_part": "week_part",
    "pickup_zone": "text",
    "dropoff_zone": "text",
    "pickup_borough": "borough",
    "dropoff_borough": "borough",
    "payment_type": "payment_code",
    "rate_code": "rate_code",
    "vendor": "vendor_code",
    "passenger_count_min": "int",
    "passenger_count_max": "int",
    "trip_distance_min": "number",
    "trip_distance_max": "number",
    "trip_duration_min": "number",
    "trip_duration_max": "number",
    "fare_amount_min": "number",
    "fare_amount_max": "number",
    "airport_trip": "bool",
    "valid_records_only": "bool",
}
GOVERNED_TOOLS = frozenset(
    {
        "query_taxi_data",
        "average_trip_metrics",
        "describe_taxi_dataset",
        "list_taxi_dimension_values",
        "aggregate_taxi_data",
        "compare_taxi_segments",
    }
)
NON_TOOL_OUTCOMES = frozenset({"no_tool", "unsupported", "clarification_required"})
PLANNER_OUTCOMES = frozenset(
    {"tool_call", "no_tool", "unsupported", "clarification_required"}
)


def classify_non_tool_outcome(text: str, question: str = "") -> str:
    """Classify a non-tool planner response into a typed outcome:
    'unsupported' | 'clarification_required' | 'no_tool'.
    """
    combined = f"{question} {text}".lower()
    if any(
        phrase in combined
        for phrase in (
            "please clarify",
            "could you clarify",
            "which borough did you mean",
            "did you mean",
            "please specify",
            "clarification required",
            "need more details",
        )
    ):
        return "clarification_required"
    if any(
        phrase in combined
        for phrase in (
            "cannot",
            "can't",
            "not available",
            "does not contain",
            "no data",
            "outside",
            "unable",
            "not possible",
            "unsupported",
            "read-only",
            "no driver",
            "no weather",
            "predict",
            "forecast",
        )
    ):
        return "unsupported"
    return "no_tool"


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Violation:
    code: str
    message: str  # deterministic template text only; never echoes model strings
    recoverable: bool = True


@dataclass(frozen=True)
class ProposalValidation:
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    outcome: str = "tool_call"

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def recoverable(self) -> bool:
        """True only when there are violations and every one is fixable by the model."""
        return bool(self.violations) and all(v.recoverable for v in self.violations)

    def feedback(self) -> str:
        return "\n".join(f"- {v.code}: {v.message}" for v in self.violations[:5])


class _Collector:
    def __init__(self) -> None:
        self.items: list[Violation] = []

    def add(self, code: str, message: str, recoverable: bool = True) -> None:
        self.items.append(Violation(code, message, recoverable))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_keys(
    args: Mapping[str, Any], allowed: set[str], required: set[str], out: _Collector
) -> None:
    if set(args) - allowed:
        out.add(
            "unexpected_argument",
            f"only these arguments are allowed: {sorted(allowed)}",
        )
    for key in sorted(required - set(args)):
        out.add("missing_argument", f"required argument '{key}' is missing")


def _check_limit(args: Mapping[str, Any], out: _Collector) -> None:
    if "limit" not in args:
        return
    limit = args["limit"]
    if not _is_int(limit):
        out.add("invalid_type", "limit must be an integer")
    elif not 1 <= limit <= MAX_ROWS:
        out.add("invalid_value", f"limit must be between 1 and {MAX_ROWS}")


def _check_name_list(
    values: object, allowed: frozenset[str], label: str, code: str, out: _Collector
) -> None:
    if not isinstance(values, list) or not values:
        out.add("invalid_type", f"{label} must be a non-empty list")
        return
    if len(values) > MAX_LIST_LENGTH:
        out.add("invalid_value", f"{label} allows at most {MAX_LIST_LENGTH} items")
        return
    if any(not isinstance(v, str) for v in values):
        out.add("invalid_type", f"{label} must contain strings")
        return
    if len(set(values)) != len(values):
        out.add("invalid_value", f"{label} must not repeat")
    if any(v not in allowed for v in values):
        out.add(code, f"{label} must be drawn from the allowlisted names")


def _parse_ts(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value or len(value) > 32:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _month_bounds(
    schema: Mapping[str, object] | None,
) -> tuple[datetime.datetime, datetime.datetime] | None:
    month = schema.get("month") if schema else None
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        return None
    year, mon = int(month[:4]), int(month[5:])
    if not 1 <= mon <= 12:
        return None
    start = datetime.datetime(year, mon, 1)
    end = datetime.datetime(year + (mon == 12), mon % 12 + 1, 1)
    return start, end


def _check_filters(
    filters: object,
    schema: Mapping[str, object] | None,
    out: _Collector,
    label: str = "filters",
) -> None:
    if filters is None:
        return
    if not isinstance(filters, Mapping):
        out.add("invalid_type", f"{label} must be an object")
        return
    if set(filters) - set(_FILTER_KINDS):
        out.add("unknown_filter", f"{label} may only use allowlisted filter fields")
    timestamps: dict[str, datetime.datetime] = {}
    ints: dict[str, int] = {}
    for key, value in filters.items():
        kind = _FILTER_KINDS.get(key)
        if kind is None or value is None:
            continue
        if kind == "timestamp":
            ts = _parse_ts(value)
            if ts is None:
                out.add("invalid_value", f"{key} must be an ISO-8601 timestamp")
            else:
                timestamps[key] = ts
        elif kind == "hour":
            if not _is_int(value):
                out.add("invalid_type", f"{key} must be an integer")
            elif not 0 <= value <= 23:
                out.add("invalid_value", f"{key} must be between 0 and 23")
            else:
                ints[key] = value
        elif kind in ("weekday", "week_part", "borough", "text"):
            _check_text_filter(key, kind, value, out)
        elif kind in ("payment_code", "rate_code", "vendor_code"):
            codes = {
                "payment_code": PAYMENT_TYPE_CODES,
                "rate_code": RATE_CODE_CODES,
                "vendor_code": VENDOR_CODES,
            }[kind]
            if not _is_int(value):
                out.add("invalid_type", f"{key} must be an integer code")
            elif value not in codes:
                out.add("invalid_value", f"{key} must be a documented code")
        elif kind == "int":
            if not _is_int(value):
                out.add("invalid_type", f"{key} must be an integer")
        elif kind == "number":
            if not _is_number(value):
                out.add("invalid_type", f"{key} must be a number")
        elif kind == "bool" and not isinstance(value, bool):
            out.add("invalid_type", f"{key} must be a boolean")
    _check_time_scope(timestamps, schema, out)
    if (
        "pickup_hour_min" in ints
        and "pickup_hour_max" in ints
        and ints["pickup_hour_min"] > ints["pickup_hour_max"]
    ):
        out.add("invalid_range", "pickup_hour_min must not exceed pickup_hour_max")


def _check_text_filter(key: str, kind: str, value: object, out: _Collector) -> None:
    if not isinstance(value, str):
        out.add("invalid_type", f"{key} must be a string")
        return
    if not value.strip() or len(value) > MAX_STRING_LENGTH or _CONTROL.search(value):
        out.add("invalid_value", f"{key} must be a short single-line string")
    elif kind == "borough" and value not in BOROUGHS:
        out.add("invalid_value", f"{key} must be one of {list(BOROUGHS)}")
    elif kind == "weekday" and value not in WEEKDAYS:
        out.add("invalid_value", f"{key} must be a full English weekday name")
    elif kind == "week_part" and value not in WEEK_PARTS:
        out.add("invalid_value", f"{key} must be 'weekday' or 'weekend'")


def _check_time_scope(
    timestamps: dict[str, datetime.datetime],
    schema: Mapping[str, object] | None,
    out: _Collector,
) -> None:
    start = timestamps.get("start_timestamp")
    end = timestamps.get("end_timestamp")
    if start and end and start > end:
        out.add("invalid_range", "start_timestamp must not be after end_timestamp")
    bounds = _month_bounds(schema)
    if bounds is None:
        return
    lo, hi = bounds
    if any(ts < lo or ts > hi for ts in timestamps.values()):
        # Pinned single-month dataset: other periods cannot be fixed by retrying.
        out.add(
            "out_of_scope_time",
            f"the dataset only covers {lo:%Y-%m}; other periods are unsupported",
            recoverable=False,
        )


def _validate_query_taxi_data(args: Mapping[str, Any], out: _Collector) -> None:
    _check_keys(args, {"analysis", "limit"}, {"analysis", "limit"}, out)
    analysis = args.get("analysis")
    if "analysis" in args:
        if not isinstance(analysis, str):
            out.add("invalid_type", "analysis must be a string")
        elif analysis not in ANALYSES:
            out.add("invalid_value", f"analysis must be one of {list(ANALYSES)}")
    _check_limit(args, out)


def _validate_average_trip_metrics(args: Mapping[str, Any], out: _Collector) -> None:
    _check_keys(args, {"region_name"}, set(), out)
    region = args.get("region_name")
    if region is None:
        return
    if not isinstance(region, str):
        out.add("invalid_type", "region_name must be a string")
    elif region not in BOROUGHS:
        out.add(
            "invalid_value",
            f"region_name must be exactly one of {list(BOROUGHS)} or omitted; "
            "for a zone or other filter use aggregate_taxi_data with filters",
        )


def _validate_describe(args: Mapping[str, Any], out: _Collector) -> None:
    _check_keys(args, {"include_column_stats"}, set(), out)
    if not isinstance(args.get("include_column_stats", False), bool):
        out.add("invalid_type", "include_column_stats must be a boolean")


def _validate_list_values(args: Mapping[str, Any], out: _Collector) -> None:
    _check_keys(args, {"dimension", "search", "limit"}, {"dimension"}, out)
    dimension = args.get("dimension")
    if "dimension" in args:
        if not isinstance(dimension, str):
            out.add("invalid_type", "dimension must be a string")
        elif dimension not in DIMENSIONS:
            out.add("unknown_dimension", "dimension must be an allowlisted dimension")
    search = args.get("search")
    if search is not None and (
        not isinstance(search, str) or _CONTROL.search(search) or len(search) > 128
    ):
        out.add("invalid_value", "search must be a short single-line string")
    _check_limit(args, out)


def _validate_aggregate(
    args: Mapping[str, Any], schema: Mapping[str, object] | None, out: _Collector
) -> None:
    _check_keys(
        args,
        {"dimensions", "measures", "filters", "order_by", "limit"},
        {"dimensions", "measures"},
        out,
    )
    if "dimensions" in args:
        _check_name_list(
            args["dimensions"], DIMENSIONS, "dimensions", "unknown_dimension", out
        )
    if "measures" in args:
        _check_name_list(args["measures"], MEASURES, "measures", "unknown_measure", out)
    _check_filters(args.get("filters"), schema, out)
    order_by = args.get("order_by")
    if order_by is not None:
        if not isinstance(order_by, Mapping):
            out.add("invalid_type", "order_by must be an object")
        else:
            if set(order_by) - {"key", "direction"}:
                out.add("unexpected_argument", "order_by allows only key and direction")
            if order_by.get("direction", "desc") not in ("asc", "desc"):
                out.add("invalid_value", "order_by.direction must be 'asc' or 'desc'")
            key = order_by.get("key")
            if not isinstance(key, str) or key not in DIMENSIONS | MEASURES:
                out.add(
                    "invalid_value",
                    "order_by.key must be an allowlisted dimension or measure",
                )
    _check_limit(args, out)


def _validate_compare(
    args: Mapping[str, Any], schema: Mapping[str, object] | None, out: _Collector
) -> None:
    required = {
        "segment_dimension",
        "measures",
        "baseline_filters",
        "comparison_filters",
    }
    _check_keys(args, required | {"limit"}, required, out)
    segment = args.get("segment_dimension")
    if "segment_dimension" in args:
        if not isinstance(segment, str):
            out.add("invalid_type", "segment_dimension must be a string")
        elif segment not in DIMENSIONS:
            out.add(
                "unknown_dimension",
                "segment_dimension must be an allowlisted dimension",
            )
    if "measures" in args:
        _check_name_list(args["measures"], MEASURES, "measures", "unknown_measure", out)
    for side in ("baseline_filters", "comparison_filters"):
        if side in args:
            if not isinstance(args[side], Mapping):
                out.add("invalid_type", f"{side} must be an object")
            else:
                _check_filters(args[side], schema, out, side)
    _check_limit(args, out)


def validate_proposal(
    name: str,
    arguments: object,
    *,
    schema: Mapping[str, object] | None = None,
    governed_calls_executed: int = 0,
    max_governed_analyses: int = 1,
) -> ProposalValidation:
    """Return every violation of a proposed governed tool call (empty -> safe to execute)."""
    out = _Collector()
    if name in NON_TOOL_OUTCOMES:
        return ProposalValidation(tuple(out.items), outcome=name)
    if not name:
        return ProposalValidation(tuple(out.items), outcome="no_tool")
    if name not in GOVERNED_TOOLS:
        out.add("unknown_tool", f"tool must be one of {sorted(GOVERNED_TOOLS)}")
        return ProposalValidation(tuple(out.items), outcome="tool_call")
    if governed_calls_executed >= max_governed_analyses:
        out.add(
            "max_governed_analyses_exceeded",
            f"at most {max_governed_analyses} governed analysis per run",
            recoverable=False,
        )
        return ProposalValidation(tuple(out.items))
    if arguments is None and name in ("average_trip_metrics", "describe_taxi_dataset"):
        arguments = {}
    if not isinstance(arguments, Mapping):
        out.add("arguments_not_object", "tool arguments must be a JSON object")
        return ProposalValidation(tuple(out.items))
    if name == "query_taxi_data":
        _validate_query_taxi_data(arguments, out)
    elif name == "average_trip_metrics":
        _validate_average_trip_metrics(arguments, out)
    elif name == "describe_taxi_dataset":
        _validate_describe(arguments, out)
    elif name == "list_taxi_dimension_values":
        _validate_list_values(arguments, out)
    elif name == "aggregate_taxi_data":
        _validate_aggregate(arguments, schema, out)
    else:
        _validate_compare(arguments, schema, out)
    return ProposalValidation(tuple(out.items))


def summarize_arguments(
    arguments: object, *, include_content: bool = False
) -> dict[str, object]:
    """Durable-safe description of model-supplied arguments (no raw values by default)."""
    import hashlib
    import json

    try:
        blob = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(type(arguments))
    summary: dict[str, object] = {
        "argument_keys": (
            sorted(str(k) for k in arguments)[:32]
            if isinstance(arguments, Mapping)
            else []
        ),
        "arguments_sha256": hashlib.sha256(blob.encode()).hexdigest(),
    }
    if include_content:
        summary["arguments"] = blob[:1000]
    return summary

"""Model-facing JSON schemas for the governed analytics tools.

These only change the *published* inputSchema. Runtime validation stays loose in
the tool signatures and `dataset_spike.query_spec` remains the single source of
truth (including the ``QueryValidationError`` envelopes).
"""

from __future__ import annotations

from typing import Annotated, Literal

from dataset_spike.analytics import COMPARISON_MODES
from dataset_spike.query_spec import (
    PAYMENT_TYPE_CODES,
    RATE_CODE_CODES,
    VENDOR_CODES,
    DimensionName,
    MeasureName,
)
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema


def _codes(codes: dict[int, str]) -> str:
    return ", ".join(f"{code}={name}" for code, name in codes.items())


class TaxiFilters(BaseModel):
    """Optional filters; omitted fields are unrestricted. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    start_timestamp: str | None = Field(
        None, description="Inclusive ISO-8601 pickup start, e.g. 2024-01-15T08:00:00."
    )
    end_timestamp: str | None = Field(
        None, description="ISO-8601 pickup end, e.g. 2024-01-16T00:00:00."
    )
    pickup_hour_min: int | None = Field(
        None, ge=0, le=23, description="Earliest pickup hour of day (0-23)."
    )
    pickup_hour_max: int | None = Field(
        None, ge=0, le=23, description="Latest pickup hour of day (0-23)."
    )
    pickup_weekday: (
        Literal[
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        | None
    ) = Field(None, description="Full weekday name of the pickup.")
    pickup_week_part: Literal["weekday", "weekend"] | None = Field(
        None, description="Whether the pickup was on a weekday or weekend."
    )
    pickup_zone: str | None = Field(None, description="Exact pickup zone name.")
    pickup_borough: str | None = Field(None, description="Exact pickup borough name.")
    dropoff_zone: str | None = Field(None, description="Exact dropoff zone name.")
    dropoff_borough: str | None = Field(None, description="Exact dropoff borough name.")
    payment_type: int | None = Field(
        None, description=f"Payment type code: {_codes(PAYMENT_TYPE_CODES)}."
    )
    rate_code: int | None = Field(
        None, description=f"Rate code: {_codes(RATE_CODE_CODES)}."
    )
    vendor: int | None = Field(
        None, description=f"Vendor code: {_codes(VENDOR_CODES)}."
    )
    passenger_count_min: int | None = Field(None, description="Minimum passengers.")
    passenger_count_max: int | None = Field(None, description="Maximum passengers.")
    trip_distance_min: float | None = Field(None, description="Minimum miles.")
    trip_distance_max: float | None = Field(None, description="Maximum miles.")
    trip_duration_min: float | None = Field(None, description="Minimum minutes.")
    trip_duration_max: float | None = Field(None, description="Maximum minutes.")
    fare_amount_min: float | None = Field(None, description="Minimum fare in USD.")
    fare_amount_max: float | None = Field(None, description="Maximum fare in USD.")
    airport_trip: bool | None = Field(
        None, description="True for airport trips only, false to exclude them."
    )
    valid_records_only: bool = Field(
        True, description="Drop records failing data-quality checks (default true)."
    )


class OrderBy(BaseModel):
    """Sort by one of the requested output columns (a dimension or measure)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Requested dimension or measure name to sort by.")
    direction: Literal["asc", "desc"] = "desc"


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _inline(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


_DIMENSION = {"type": "string", "enum": [d.value for d in DimensionName]}
_MEASURE = {"type": "string", "enum": [m.value for m in MeasureName]}

FiltersSchema = Annotated[dict[str, object], WithJsonSchema(_inline(TaxiFilters))]
OptionalFiltersSchema = Annotated[
    dict[str, object] | None, WithJsonSchema(_nullable(_inline(TaxiFilters)))
]
OptionalOrderBySchema = Annotated[
    dict[str, object] | None, WithJsonSchema(_nullable(_inline(OrderBy)))
]
DimensionsSchema = Annotated[
    list[str], WithJsonSchema({"type": "array", "items": _DIMENSION})
]
MeasuresSchema = Annotated[
    list[str], WithJsonSchema({"type": "array", "items": _MEASURE})
]
OptionalDimensionSchema = Annotated[str | None, WithJsonSchema(_nullable(_DIMENSION))]
DimensionSchema = Annotated[str, WithJsonSchema(_DIMENSION)]
OptionalComparisonModeSchema = Annotated[
    str | None,
    WithJsonSchema(
        _nullable(
            {
                "type": "string",
                "enum": list(COMPARISON_MODES),
                "description": "by_dimension (default) needs segment_dimension; "
                "whole_segment forbids it.",
            }
        )
    ),
]

"""Typed, allowlisted query spec for governed taxi analytics.

Every field here maps 1:1 onto a fixed SQL fragment compiled in
``query_compiler.py``. Nothing derived from caller strings ever reaches
DuckDB: dimensions, measures, filter fields and operators are all closed
enums, and encoded values (payment/rate/vendor codes, boroughs, zones) are
validated against fixed dictionaries before compilation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

MAX_ROWS = 20
MAX_COLUMNS = 16
MAX_LIST_LENGTH = 16
MAX_STRING_LENGTH = 128


class QueryValidationError(Exception):
    """A structured, non-retryable validation failure raised before any DuckDB work."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_envelope(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": False,
            }
        }


class DimensionName(StrEnum):
    PICKUP_DATE = "pickup_date"
    PICKUP_HOUR = "pickup_hour"
    PICKUP_WEEKDAY = "pickup_weekday"
    PICKUP_WEEK_PART = "pickup_week_part"
    PICKUP_ZONE = "pickup_zone"
    PICKUP_BOROUGH = "pickup_borough"
    DROPOFF_ZONE = "dropoff_zone"
    DROPOFF_BOROUGH = "dropoff_borough"
    PAYMENT_TYPE = "payment_type"
    RATE_CODE = "rate_code"
    VENDOR = "vendor"
    PASSENGER_COUNT_BUCKET = "passenger_count_bucket"
    TRIP_DISTANCE_BUCKET = "trip_distance_bucket"
    TRIP_DURATION_BUCKET = "trip_duration_bucket"
    FARE_AMOUNT_BUCKET = "fare_amount_bucket"
    AIRPORT_TRIP = "airport_trip"


class MeasureName(StrEnum):
    TRIP_COUNT = "trip_count"
    TRIP_SHARE = "trip_share"
    AVERAGE_FARE = "average_fare"
    MEDIAN_FARE = "median_fare"
    AVERAGE_TIP = "average_tip"
    MEDIAN_TIP = "median_tip"
    TOTAL_TIPS = "total_tips"
    TIP_RATE = "tip_rate"
    AVERAGE_TOTAL_AMOUNT = "average_total_amount"
    AVERAGE_DISTANCE = "average_distance"
    MEDIAN_DISTANCE = "median_distance"
    AVERAGE_DURATION_MINUTES = "average_duration_minutes"
    MEDIAN_DURATION_MINUTES = "median_duration_minutes"
    P95_DURATION_MINUTES = "p95_duration_minutes"
    AVERAGE_PASSENGER_COUNT = "average_passenger_count"


class FilterField(StrEnum):
    START_TIMESTAMP = "start_timestamp"
    END_TIMESTAMP = "end_timestamp"
    PICKUP_HOUR_MIN = "pickup_hour_min"
    PICKUP_HOUR_MAX = "pickup_hour_max"
    PICKUP_WEEKDAY = "pickup_weekday"
    PICKUP_WEEK_PART = "pickup_week_part"
    PICKUP_ZONE = "pickup_zone"
    PICKUP_BOROUGH = "pickup_borough"
    DROPOFF_ZONE = "dropoff_zone"
    DROPOFF_BOROUGH = "dropoff_borough"
    PAYMENT_TYPE = "payment_type"
    RATE_CODE = "rate_code"
    VENDOR = "vendor"
    PASSENGER_COUNT_MIN = "passenger_count_min"
    PASSENGER_COUNT_MAX = "passenger_count_max"
    TRIP_DISTANCE_MIN = "trip_distance_min"
    TRIP_DISTANCE_MAX = "trip_distance_max"
    TRIP_DURATION_MIN = "trip_duration_min"
    TRIP_DURATION_MAX = "trip_duration_max"
    FARE_AMOUNT_MIN = "fare_amount_min"
    FARE_AMOUNT_MAX = "fare_amount_max"
    AIRPORT_TRIP = "airport_trip"
    VALID_RECORDS_ONLY = "valid_records_only"


WEEKDAY_NAMES = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)
WEEK_PARTS = frozenset({"weekday", "weekend"})
# Documented, closed code dictionaries. `0`/`99`/null/`6` are explicit "unknown" codes.
PAYMENT_TYPE_CODES: dict[int, str] = {
    0: "unknown_or_flex_fare",
    1: "credit_card",
    2: "cash",
    3: "no_charge",
    4: "dispute",
    5: "unknown_reserved",
    6: "voided_trip",
}
RATE_CODE_CODES: dict[int, str] = {
    1: "standard",
    2: "jfk",
    3: "newark",
    4: "nassau_or_westchester",
    5: "negotiated",
    6: "group_ride",
    99: "unknown",
}
VENDOR_CODES: dict[int, str] = {
    1: "creative_mobile_technologies",
    2: "curb_mobility",
    6: "myle_technologies",
}
AIRPORT_ZONE_NAMES = frozenset({"JFK Airport", "LaGuardia Airport", "Newark Airport"})
AIRPORT_RATE_CODES = frozenset({2, 3})

# Bucket edges are fixed and documented; callers cannot influence them.
PASSENGER_COUNT_BUCKET_EDGES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)
TRIP_DISTANCE_BUCKET_EDGES: tuple[float, ...] = (1, 2, 5, 10, 20)
TRIP_DURATION_BUCKET_EDGES: tuple[float, ...] = (5, 10, 20, 30, 60)
FARE_AMOUNT_BUCKET_EDGES: tuple[float, ...] = (10, 20, 40, 75, 150)

_DIMENSIONS_REQUIRING_ZONE_JOIN = frozenset(
    {
        DimensionName.PICKUP_ZONE,
        DimensionName.PICKUP_BOROUGH,
        DimensionName.DROPOFF_ZONE,
        DimensionName.DROPOFF_BOROUGH,
        DimensionName.AIRPORT_TRIP,
    }
)
_FILTERS_REQUIRING_ZONE_JOIN = frozenset(
    {
        FilterField.PICKUP_ZONE,
        FilterField.PICKUP_BOROUGH,
        FilterField.DROPOFF_ZONE,
        FilterField.DROPOFF_BOROUGH,
        FilterField.AIRPORT_TRIP,
    }
)


def requires_zone_join(
    dimensions: list[DimensionName], filters: TaxiFilters | None
) -> bool:
    if any(dimension in _DIMENSIONS_REQUIRING_ZONE_JOIN for dimension in dimensions):
        return True
    if filters is None:
        return False
    return filters.uses_zone_join()


@dataclass(frozen=True)
class TaxiFilters:
    start_timestamp: str | None = None
    end_timestamp: str | None = None
    pickup_hour_min: int | None = None
    pickup_hour_max: int | None = None
    pickup_weekday: str | None = None
    pickup_week_part: str | None = None
    pickup_zone: str | None = None
    pickup_borough: str | None = None
    dropoff_zone: str | None = None
    dropoff_borough: str | None = None
    payment_type: int | None = None
    rate_code: int | None = None
    vendor: int | None = None
    passenger_count_min: int | None = None
    passenger_count_max: int | None = None
    trip_distance_min: float | None = None
    trip_distance_max: float | None = None
    trip_duration_min: float | None = None
    trip_duration_max: float | None = None
    fare_amount_min: float | None = None
    fare_amount_max: float | None = None
    airport_trip: bool | None = None
    valid_records_only: bool = True

    def uses_zone_join(self) -> bool:
        return any(
            value is not None
            for value in (
                self.pickup_zone,
                self.pickup_borough,
                self.dropoff_zone,
                self.dropoff_borough,
                self.airport_trip,
            )
        )


@dataclass(frozen=True)
class OrderSpec:
    key: str  # a DimensionName or MeasureName value present in the query's own output
    direction: str = "desc"  # "asc" | "desc"


@dataclass(frozen=True)
class AggregateQuerySpec:
    dimensions: list[DimensionName] = field(default_factory=list)
    measures: list[MeasureName] = field(default_factory=list)
    filters: TaxiFilters | None = None
    order_by: OrderSpec | None = None
    limit: int = MAX_ROWS


def _fail(code: str, message: str) -> None:
    raise QueryValidationError(code, message)


def validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ROWS:
        _fail("invalid_limit", f"limit must be an integer between 1 and {MAX_ROWS}")
    return limit  # type: ignore[return-value]


def validate_dimension(value: object) -> DimensionName:
    if isinstance(value, DimensionName):
        return value
    try:
        return DimensionName(value)
    except ValueError:
        _fail("unknown_dimension", f"'{value}' is not an allowlisted dimension")
    raise AssertionError("unreachable")


def validate_measure(value: object) -> MeasureName:
    if isinstance(value, MeasureName):
        return value
    try:
        return MeasureName(value)
    except ValueError:
        _fail("unknown_measure", f"'{value}' is not an allowlisted measure")
    raise AssertionError("unreachable")


def validate_dimensions(values: object) -> list[DimensionName]:
    if not isinstance(values, list) or not values:
        _fail("invalid_dimensions", "dimensions must be a non-empty list")
    if len(values) > MAX_LIST_LENGTH:
        _fail("invalid_dimensions", f"at most {MAX_LIST_LENGTH} dimensions are allowed")
    dimensions = [validate_dimension(value) for value in values]
    if len(set(dimensions)) != len(dimensions):
        _fail("invalid_dimensions", "dimensions must not repeat")
    return dimensions


def validate_measures(values: object) -> list[MeasureName]:
    if not isinstance(values, list) or not values:
        _fail("invalid_measures", "measures must be a non-empty list")
    if len(values) > MAX_LIST_LENGTH:
        _fail("invalid_measures", f"at most {MAX_LIST_LENGTH} measures are allowed")
    measures = [validate_measure(value) for value in values]
    if len(set(measures)) != len(measures):
        _fail("invalid_measures", "measures must not repeat")
    return measures


def validate_column_budget(
    dimensions: list[DimensionName], measures: list[MeasureName]
) -> None:
    if len(dimensions) + len(measures) > MAX_COLUMNS:
        _fail(
            "invalid_column_budget",
            f"dimensions plus measures must not exceed {MAX_COLUMNS} columns",
        )


def _validate_iso_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 32:
        _fail("invalid_filter_value", f"{field_name} must be an ISO-8601 timestamp string")
    import datetime

    try:
        datetime.datetime.fromisoformat(value)  # type: ignore[arg-type]
    except ValueError:
        _fail("invalid_filter_value", f"{field_name} is not a valid ISO-8601 timestamp")
    return value  # type: ignore[return-value]


def _validate_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_STRING_LENGTH:
        _fail("invalid_filter_value", f"{field_name} must be a non-empty bounded string")
    return value.strip()  # type: ignore[return-value]


def _validate_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_filter_value", f"{field_name} must be an integer")
    return value  # type: ignore[return-value]


def _validate_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("invalid_filter_value", f"{field_name} must be a number")
    return float(value)  # type: ignore[arg-type]


def validate_filters(payload: object) -> TaxiFilters | None:
    if payload is None:
        return TaxiFilters()
    if not isinstance(payload, dict):
        _fail("invalid_filters", "filters must be an object")
    unknown = set(payload) - {field_.value for field_ in FilterField}
    if unknown:
        _fail("unknown_filter", f"unknown filter field(s): {sorted(unknown)}")

    start_timestamp = payload.get("start_timestamp")
    end_timestamp = payload.get("end_timestamp")
    if start_timestamp is not None:
        start_timestamp = _validate_iso_timestamp(start_timestamp, "start_timestamp")
    if end_timestamp is not None:
        end_timestamp = _validate_iso_timestamp(end_timestamp, "end_timestamp")

    pickup_hour_min = payload.get("pickup_hour_min")
    pickup_hour_max = payload.get("pickup_hour_max")
    for name, value in (("pickup_hour_min", pickup_hour_min), ("pickup_hour_max", pickup_hour_max)):
        if value is not None:
            hour = _validate_int(value, name)
            if not 0 <= hour <= 23:
                _fail("invalid_filter_value", f"{name} must be between 0 and 23")

    pickup_weekday = payload.get("pickup_weekday")
    if pickup_weekday is not None:
        pickup_weekday = _validate_string(pickup_weekday, "pickup_weekday")
        if pickup_weekday not in WEEKDAY_NAMES:
            _fail("invalid_filter_value", "pickup_weekday must be a full weekday name")

    pickup_week_part = payload.get("pickup_week_part")
    if pickup_week_part is not None:
        pickup_week_part = _validate_string(pickup_week_part, "pickup_week_part")
        if pickup_week_part not in WEEK_PARTS:
            _fail("invalid_filter_value", "pickup_week_part must be weekday or weekend")

    pickup_zone = payload.get("pickup_zone")
    if pickup_zone is not None:
        pickup_zone = _validate_string(pickup_zone, "pickup_zone")
    pickup_borough = payload.get("pickup_borough")
    if pickup_borough is not None:
        pickup_borough = _validate_string(pickup_borough, "pickup_borough")
    dropoff_zone = payload.get("dropoff_zone")
    if dropoff_zone is not None:
        dropoff_zone = _validate_string(dropoff_zone, "dropoff_zone")
    dropoff_borough = payload.get("dropoff_borough")
    if dropoff_borough is not None:
        dropoff_borough = _validate_string(dropoff_borough, "dropoff_borough")

    payment_type = payload.get("payment_type")
    if payment_type is not None:
        payment_type = _validate_int(payment_type, "payment_type")
        if payment_type not in PAYMENT_TYPE_CODES:
            _fail("invalid_filter_value", "payment_type is not a known code")

    rate_code = payload.get("rate_code")
    if rate_code is not None:
        rate_code = _validate_int(rate_code, "rate_code")
        if rate_code not in RATE_CODE_CODES:
            _fail("invalid_filter_value", "rate_code is not a known code")

    vendor = payload.get("vendor")
    if vendor is not None:
        vendor = _validate_int(vendor, "vendor")
        if vendor not in VENDOR_CODES:
            _fail("invalid_filter_value", "vendor is not a known code")

    passenger_count_min = payload.get("passenger_count_min")
    passenger_count_max = payload.get("passenger_count_max")
    if passenger_count_min is not None:
        passenger_count_min = _validate_int(passenger_count_min, "passenger_count_min")
    if passenger_count_max is not None:
        passenger_count_max = _validate_int(passenger_count_max, "passenger_count_max")

    trip_distance_min = payload.get("trip_distance_min")
    trip_distance_max = payload.get("trip_distance_max")
    if trip_distance_min is not None:
        trip_distance_min = _validate_number(trip_distance_min, "trip_distance_min")
    if trip_distance_max is not None:
        trip_distance_max = _validate_number(trip_distance_max, "trip_distance_max")

    trip_duration_min = payload.get("trip_duration_min")
    trip_duration_max = payload.get("trip_duration_max")
    if trip_duration_min is not None:
        trip_duration_min = _validate_number(trip_duration_min, "trip_duration_min")
    if trip_duration_max is not None:
        trip_duration_max = _validate_number(trip_duration_max, "trip_duration_max")

    fare_amount_min = payload.get("fare_amount_min")
    fare_amount_max = payload.get("fare_amount_max")
    if fare_amount_min is not None:
        fare_amount_min = _validate_number(fare_amount_min, "fare_amount_min")
    if fare_amount_max is not None:
        fare_amount_max = _validate_number(fare_amount_max, "fare_amount_max")

    airport_trip = payload.get("airport_trip")
    if airport_trip is not None and not isinstance(airport_trip, bool):
        _fail("invalid_filter_value", "airport_trip must be a boolean")

    valid_records_only = payload.get("valid_records_only", True)
    if not isinstance(valid_records_only, bool):
        _fail("invalid_filter_value", "valid_records_only must be a boolean")

    return TaxiFilters(
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        pickup_hour_min=pickup_hour_min,
        pickup_hour_max=pickup_hour_max,
        pickup_weekday=pickup_weekday,
        pickup_week_part=pickup_week_part,
        pickup_zone=pickup_zone,
        pickup_borough=pickup_borough,
        dropoff_zone=dropoff_zone,
        dropoff_borough=dropoff_borough,
        payment_type=payment_type,
        rate_code=rate_code,
        vendor=vendor,
        passenger_count_min=passenger_count_min,
        passenger_count_max=passenger_count_max,
        trip_distance_min=trip_distance_min,
        trip_distance_max=trip_distance_max,
        trip_duration_min=trip_duration_min,
        trip_duration_max=trip_duration_max,
        fare_amount_min=fare_amount_min,
        fare_amount_max=fare_amount_max,
        airport_trip=airport_trip,
        valid_records_only=valid_records_only,
    )


def validate_order_by(
    payload: object, allowed_keys: frozenset[str]
) -> OrderSpec | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        _fail("invalid_order_by", "order_by must be an object")
    unknown = set(payload) - {"key", "direction"}
    if unknown:
        _fail("invalid_order_by", f"unknown order_by field(s): {sorted(unknown)}")
    key = payload.get("key")
    direction = payload.get("direction", "desc")
    if not isinstance(key, str) or key not in allowed_keys:
        _fail("invalid_order_by", "order_by.key must be one of the requested output columns")
    if direction not in ("asc", "desc"):
        _fail("invalid_order_by", "order_by.direction must be 'asc' or 'desc'")
    return OrderSpec(key=key, direction=direction)


def validate_search(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_STRING_LENGTH:
        _fail("invalid_search", "search must be a non-empty bounded string")
    return value


def build_aggregate_spec(
    *,
    dimensions: object,
    measures: object,
    filters: object,
    order_by: object,
    limit: object,
) -> AggregateQuerySpec:
    validated_dimensions = validate_dimensions(dimensions)
    validated_measures = validate_measures(measures)
    validate_column_budget(validated_dimensions, validated_measures)
    validated_filters = validate_filters(filters)
    allowed_order_keys = frozenset(
        {dimension.value for dimension in validated_dimensions}
        | {measure.value for measure in validated_measures}
    )
    validated_order_by = validate_order_by(order_by, allowed_order_keys)
    validated_limit = validate_limit(limit)
    return AggregateQuerySpec(
        dimensions=validated_dimensions,
        measures=validated_measures,
        filters=validated_filters,
        order_by=validated_order_by,
        limit=validated_limit,
    )

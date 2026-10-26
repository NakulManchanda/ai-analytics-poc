import pytest
from app import mcp_client
from app.mcp_client import (
    FastMCPDatasetProfileClient,
    MCPToolError,
    sanitize_describe_result,
    sanitize_governed_query_result,
)
from app.validation.proposal import MAX_ROWS


def _valid_describe_payload() -> dict[str, object]:
    return {
        "query_class": "describe",
        "row_count": 5,
        "min_pickup_datetime": "2024-01-01 00:00:00",
        "max_pickup_datetime": "2024-01-31 23:59:00",
        "columns": [{"name": "pickup_zone", "type": "VARCHAR"}],
        "supported_dimensions": ["pickup_hour"],
        "supported_measures": ["trip_count"],
        "code_dictionaries": {
            "payment_type": {"0": "unknown_or_flex_fare", "1": "credit_card"},
            "rate_code": {"1": "standard", "99": "unknown"},
            "vendor": {"1": "creative_mobile_technologies"},
        },
        "tip_rate_semantics": "card only",
        "airport_trip_rule": "jfk/lga/ewr or ratecode 2/3",
        "valid_records_rule": "jan 2024 window",
        "truncated": False,
        "query_id": "query_describe_1",
    }


def _valid_governed_query_payload() -> dict[str, object]:
    return {
        "columns": ["pickup_hour", "trip_count"],
        "rows": [[8, 2]],
        "row_count": 1,
        "execution_duration_ms": 3,
        "query_id": "query_agg_1",
        "truncated": False,
        "query_class": "aggregate",
        "dimensions": ["pickup_hour"],
        "measures": ["trip_count"],
    }


def _governed_payload_with_rows(count: int) -> dict[str, object]:
    payload = _valid_governed_query_payload()
    payload["rows"] = [[index % 24, index] for index in range(count)]
    payload["row_count"] = count
    return payload


class TestGovernedResultRowCap:
    def test_accepts_exactly_max_rows(self) -> None:
        sanitized = sanitize_governed_query_result(
            _governed_payload_with_rows(MAX_ROWS)
        )
        assert sanitized["row_count"] == MAX_ROWS

    def test_rejects_more_than_max_rows(self) -> None:
        with pytest.raises(MCPToolError) as exc_info:
            sanitize_governed_query_result(_governed_payload_with_rows(MAX_ROWS + 1))
        assert exc_info.value.retryable is False

    def test_client_cap_matches_the_validated_proposal_limit(self) -> None:
        # The planner may request up to MAX_ROWS rows; a smaller client cap
        # fails the call after the tool succeeded (#186 det-006).
        assert mcp_client.MAX_QUERY_RESULT_ROWS == MAX_ROWS


class TestSanitizeDescribeResult:
    def test_accepts_a_well_formed_payload(self) -> None:
        sanitized = sanitize_describe_result(_valid_describe_payload())
        assert sanitized["row_count"] == 5
        assert sanitized["code_dictionaries"]["payment_type"]["0"] == (
            "unknown_or_flex_fare"
        )

    def test_accepts_optional_null_and_invalid_summaries(self) -> None:
        payload = _valid_describe_payload()
        payload["null_summary"] = {"rate_code": 3}
        payload["invalid_record_summary"] = {"negative_fare_count": 1}
        sanitized = sanitize_describe_result(payload)
        assert sanitized["null_summary"] == {"rate_code": 3}

    def test_rejects_missing_required_field(self) -> None:
        payload = _valid_describe_payload()
        del payload["row_count"]
        with pytest.raises(MCPToolError):
            sanitize_describe_result(payload)

    def test_rejects_unknown_extra_field(self) -> None:
        payload = _valid_describe_payload()
        payload["unexpected_field"] = "value"
        with pytest.raises(MCPToolError):
            sanitize_describe_result(payload)

    def test_rejects_oversized_code_dictionary_value(self) -> None:
        payload = _valid_describe_payload()
        payload["code_dictionaries"]["vendor"]["999"] = "x" * 200
        with pytest.raises(MCPToolError):
            sanitize_describe_result(payload)


class TestSanitizeGovernedQueryResult:
    def test_accepts_a_well_formed_aggregate_payload(self) -> None:
        sanitized = sanitize_governed_query_result(_valid_governed_query_payload())
        assert sanitized["dimensions"] == ["pickup_hour"]
        assert sanitized["query_class"] == "aggregate"

    def test_rejects_unknown_extra_field(self) -> None:
        payload = _valid_governed_query_payload()
        payload["unexpected_field"] = "value"
        with pytest.raises(MCPToolError):
            sanitize_governed_query_result(payload)

    def test_rejects_missing_base_field(self) -> None:
        payload = _valid_governed_query_payload()
        del payload["truncated"]
        with pytest.raises(MCPToolError):
            sanitize_governed_query_result(payload)

    def test_rejects_oversized_dimensions_list(self) -> None:
        payload = _valid_governed_query_payload()
        payload["dimensions"] = [f"dim_{i}" for i in range(17)]
        with pytest.raises(MCPToolError):
            sanitize_governed_query_result(payload)

    def test_accepts_airport_trip_rows_shaped_as_string_labels(self) -> None:
        # M2 regression: airport_trip must be compiled to a string label
        # ('airport' / 'non_airport') at the SQL layer, not a Python bool,
        # or every query using this dimension is rejected here.
        payload = _valid_governed_query_payload()
        payload["columns"] = ["airport_trip", "trip_count"]
        payload["rows"] = [["airport", 3], ["non_airport", 9]]
        payload["row_count"] = 2
        payload["dimensions"] = ["airport_trip"]
        payload["airport_trip_rule"] = "jfk/lga/ewr or ratecode 2/3"
        sanitized = sanitize_governed_query_result(payload)
        assert sanitized["rows"] == [["airport", 3], ["non_airport", 9]]

    def test_rejects_a_raw_bool_row_value(self) -> None:
        # Demonstrates the failure mode M2 fixes: a Python bool in any row
        # value is rejected by this sanitizer before it ever reaches the LLM.
        payload = _valid_governed_query_payload()
        payload["columns"] = ["airport_trip", "trip_count"]
        payload["rows"] = [[True, 3]]
        payload["dimensions"] = ["airport_trip"]
        with pytest.raises(MCPToolError):
            sanitize_governed_query_result(payload)


class TestClientRejectsBadInputBeforeNetworkCalls:
    def test_describe_rejects_non_boolean_include_column_stats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        transports: list[object] = []

        class CapturingTransport:
            def __init__(self, url, *, headers):
                transports.append(self)

        monkeypatch.setattr(
            mcp_client, "StreamableHttpTransport", CapturingTransport, raising=False
        )

        with pytest.raises(MCPToolError) as exc_info:
            FastMCPDatasetProfileClient().describe_taxi_dataset(
                include_column_stats="yes"  # type: ignore[arg-type]
            )
        assert exc_info.value.retryable is False
        assert transports == []

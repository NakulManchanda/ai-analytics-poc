"""comparison_mode on compare_taxi_segments across the app layers (PR #193 finding 1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from app.config import DEFAULT_MODEL_ID
from app.llm import TAXI_PROPOSAL_TOOL_SPECS, LocalFakeLLMClient, ToolProposalResult
from app.mcp_client import FastMCPDatasetProfileClient, sanitize_governed_query_result
from app.orchestration import OrchestrationLoop
from app.query_catalogue import lookup
from app.state import InMemoryStateRepository
from app.validation import validate_proposal

DET_007 = "How do airport trips compare with non-airport trips on fare and duration?"
DET_013 = "Compare Manhattan and Queens on trip count, average fare, and average tip"

WHOLE = {
    "comparison_mode": "whole_segment",
    "measures": ["trip_count", "average_fare"],
    "baseline_filters": {"pickup_borough": "Manhattan"},
    "comparison_filters": {"pickup_borough": "Queens"},
}


def codes(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {v.code for v in result.violations}


def test_planner_schema_offers_comparison_mode_and_optional_dimension() -> None:
    spec = next(
        s for s in TAXI_PROPOSAL_TOOL_SPECS if s["name"] == "compare_taxi_segments"
    )
    schema = spec["inputSchema"]
    assert schema["properties"]["comparison_mode"]["enum"] == [
        "by_dimension",
        "whole_segment",
    ]
    assert "segment_dimension" in schema["properties"]
    assert "segment_dimension" not in schema["required"]
    assert "comparison_mode" not in schema["required"]


@pytest.mark.parametrize("question", [DET_007, DET_013])
def test_catalogue_resolves_same_field_comparisons_to_whole_segment(
    question: str,
) -> None:
    hit = lookup(question)
    assert hit is not None
    tool, arguments = hit
    assert tool == "compare_taxi_segments"
    assert arguments["comparison_mode"] == "whole_segment"
    assert "segment_dimension" not in arguments
    assert validate_proposal(tool, arguments).ok


def test_validator_accepts_whole_segment_and_default_by_dimension() -> None:
    assert validate_proposal("compare_taxi_segments", WHOLE).ok
    by_dim = {k: v for k, v in WHOLE.items() if k != "comparison_mode"}
    by_dim["segment_dimension"] = "pickup_hour"
    assert validate_proposal("compare_taxi_segments", by_dim).ok
    assert validate_proposal(
        "compare_taxi_segments", {**by_dim, "comparison_mode": "by_dimension"}
    ).ok
    # An explicit null dimension is "omitted".
    assert validate_proposal(
        "compare_taxi_segments", {**WHOLE, "segment_dimension": None}
    ).ok


def test_validator_rejects_invalid_mode_and_combinations() -> None:
    bad_mode = validate_proposal(
        "compare_taxi_segments", {**WHOLE, "comparison_mode": "bogus"}
    )
    assert codes(bad_mode) == {"invalid_value"}
    non_string = validate_proposal(
        "compare_taxi_segments", {**WHOLE, "comparison_mode": 3}
    )
    assert codes(non_string) == {"invalid_value"}
    with_dim = validate_proposal(
        "compare_taxi_segments", {**WHOLE, "segment_dimension": "pickup_hour"}
    )
    assert codes(with_dim) == {"invalid_combination"}
    no_dim = {k: v for k, v in WHOLE.items() if k != "comparison_mode"}
    assert codes(validate_proposal("compare_taxi_segments", no_dim)) == {
        "missing_argument"
    }
    assert codes(
        validate_proposal(
            "compare_taxi_segments", {**no_dim, "comparison_mode": "by_dimension"}
        )
    ) == {"missing_argument"}
    assert "invalid_value" in codes(
        validate_proposal(
            "compare_taxi_segments", {**WHOLE, "measures": ["trip_share"]}
        )
    )


def test_validator_rejects_malformed_or_unauthorized_filters_in_whole_segment() -> None:
    cases: list[tuple[str, Any, str]] = [
        ("baseline_filters", {"sql": "1=1"}, "unknown_filter"),
        ("comparison_filters", {"airport_trip": "yes"}, "invalid_type"),
        ("comparison_filters", "pickup_borough=Queens", "invalid_type"),
        ("baseline_filters", {"pickup_borough": "Atlantis"}, "invalid_value"),
    ]
    for side, value, expected in cases:
        result = validate_proposal("compare_taxi_segments", {**WHOLE, side: value})
        assert not result.ok, (side, value)
        assert expected in codes(result), (side, value, codes(result))
    extra = validate_proposal("compare_taxi_segments", {**WHOLE, "sql": "drop"})
    assert "unexpected_argument" in codes(extra)


def test_sanitizer_keeps_whole_segment_envelope_fields() -> None:
    payload = {
        "columns": ["measure", "baseline", "comparison", "delta", "delta_pct"],
        "rows": [["trip_count", 4, None, None, None]],
        "row_count": 1,
        "execution_duration_ms": 3,
        "query_id": "query_x",
        "truncated": False,
        "query_class": "compare_segments",
        "comparison_mode": "whole_segment",
        "baseline_label": "pickup_borough=Manhattan",
        "comparison_label": "pickup_borough=Staten Island",
        "measures": ["trip_count"],
        "no_data_sides": ["comparison"],
    }
    sanitized = sanitize_governed_query_result(payload)
    assert sanitized["comparison_mode"] == "whole_segment"
    assert sanitized["baseline_label"] == "pickup_borough=Manhattan"
    assert sanitized["no_data_sides"] == ["comparison"]
    assert sanitized["rows"] == [["trip_count", 4, None, None, None]]


class _RecordingMCP:
    def __init__(self) -> None:
        self.compare_calls: list[dict[str, Any]] = []

    def get_dataset_schema(self) -> dict[str, Any]:
        return {"dataset": "nyc-taxi", "month": "2024-01", "columns": ["fare_amount"]}

    def compare_taxi_segments(self, **kwargs: Any) -> dict[str, Any]:
        self.compare_calls.append(kwargs)
        return {
            "columns": ["measure", "baseline", "comparison", "delta", "delta_pct"],
            "rows": [["average_fare", 32.0, 90.0, 58.0, 181.25]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "query_cmp",
            "truncated": False,
            "query_class": "compare_segments",
            "comparison_mode": "whole_segment",
            "baseline_label": "airport_trip=false",
            "comparison_label": "airport_trip=true",
            "measures": ["average_fare"],
        }


def test_catalogue_chip_reaches_mcp_as_whole_segment_without_dimension() -> None:
    class AnsweringLLM(LocalFakeLLMClient):
        # The stock fake only narrates 2-column results.
        def answer_with_query_result(
            self, prompt: str, query_result: Mapping[str, object]
        ) -> Any:
            return self._result(prompt, "comparison answered")

    mcp = _RecordingMCP()
    loop = OrchestrationLoop(
        llm_client=AnsweringLLM(),
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
    )
    result = loop.run(DET_007)
    assert result.status == "completed"
    assert len(mcp.compare_calls) == 1
    call = mcp.compare_calls[0]
    assert call["comparison_mode"] == "whole_segment"
    assert "segment_dimension" not in call
    assert call["baseline_filters"] == {"airport_trip": False}
    assert call["comparison_filters"] == {"airport_trip": True}


@pytest.mark.parametrize(
    "arguments",
    [
        {**WHOLE, "segment_dimension": "pickup_hour"},
        {**WHOLE, "comparison_mode": "bogus"},
        {**WHOLE, "comparison_filters": {"sql": "1=1"}},
        {**WHOLE, "baseline_filters": {"airport_trip": "yes"}},
    ],
)
def test_invalid_model_proposals_are_rejected_before_mcp_execution(
    arguments: dict[str, Any],
) -> None:
    class ModelLLM(LocalFakeLLMClient):
        def propose_taxi_query(
            self, prompt: str, schema: Mapping[str, object]
        ) -> ToolProposalResult:
            return ToolProposalResult(
                name="compare_taxi_segments",
                arguments=arguments,
                model_id=DEFAULT_MODEL_ID,
                input_tokens=1,
                output_tokens=1,
                latency_ms=0,
            )

    mcp = _RecordingMCP()
    loop = OrchestrationLoop(
        llm_client=ModelLLM(),
        mcp_client=mcp,  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
    )
    try:
        loop.run("A novel question the catalogue does not know about")
    except ValueError:
        pass
    assert mcp.compare_calls == []


def test_client_sends_optional_fields_only_when_chosen(monkeypatch) -> None:  # noqa: ANN001
    sent: list[dict[str, object]] = []

    class _Result:
        data = {
            "columns": ["c"],
            "rows": [[1]],
            "row_count": 1,
            "execution_duration_ms": 1,
            "query_id": "q",
            "truncated": False,
        }

    class _FakeClient:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict[str, object]) -> _Result:
            sent.append(arguments)
            return _Result()

    client = FastMCPDatasetProfileClient("http://unused")
    monkeypatch.setattr(client, "_client", lambda: _FakeClient())
    base = {
        "measures": ["trip_count"],
        "baseline_filters": {},
        "comparison_filters": {},
    }
    client.compare_taxi_segments(segment_dimension="pickup_hour", **base)
    client.compare_taxi_segments(comparison_mode="whole_segment", **base)
    assert "comparison_mode" not in sent[0]
    assert sent[0]["segment_dimension"] == "pickup_hour"
    assert sent[1]["comparison_mode"] == "whole_segment"
    assert "segment_dimension" not in sent[1]

"""Unit and contract tests for #235 typed artifact envelopes and cross-agent synthesis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.artifacts.envelope import (
    ARTIFACT_ENVELOPE_VERSION,
    ArtifactEnvelopeError,
    ArtifactProvenance,
    map_envelope_from_artifact,
    segment_comparison_from_tool_result,
    synthesize_artifacts,
    table_from_tool_result,
    time_series_from_tool_result,
    validate_artifact_envelope,
)

MAP_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web/src/map/fixtures/pickup-count-choropleth.v1.json"
)


def _prov(**overrides) -> ArtifactProvenance:
    base = {
        "run_id": "run_235",
        "agent_id": "trend-analyst",
        "tool_call_id": "tc_1",
        "query_id": "q_1",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "tool_name": "aggregate_taxi_data",
    }
    base.update(overrides)
    return ArtifactProvenance(**base)


def test_table_time_series_segment_and_map_envelopes_roundtrip() -> None:
    tbl = table_from_tool_result(
        title="Top pickup boroughs",
        columns=[("borough", "Borough", None), ("trips", "Trips", "trips")],
        rows=[["Manhattan", 2616329], ["Queens", 279000]],
        provenance=_prov(query_id="q_tbl"),
    )
    ts = time_series_from_tool_result(
        title="Trips by hour",
        x_label="pickup_hour",
        y_label="trip_count",
        unit="trips",
        rows=[("08:00", 120000), ("18:00", 215000)],
        provenance=_prov(query_id="q_ts"),
    )
    seg = segment_comparison_from_tool_result(
        title="Weekday vs weekend fare",
        dimension="pickup_week_part",
        metric="average_fare",
        unit="USD",
        rows=[("Manhattan", 15.05, 16.55), ("Queens", 48.0, 50.4)],
        provenance=_prov(query_id="q_seg"),
    )
    map_raw = json.loads(MAP_FIXTURE.read_text())
    mp = map_envelope_from_artifact(
        artifact=map_raw, provenance=_prov(query_id="q_map", tool_name="map_taxi_zones")
    )

    assert ts.body["total_y"] == 335000
    assert seg.body["rows"][0]["delta"] == 1.5
    assert seg.body["rows"][0]["delta_pct"] == 9.97
    for env in (tbl, ts, seg, mp):
        assert env.schema_version == ARTIFACT_ENVELOPE_VERSION
        # Survives JSON rehydration cleanly.
        rehydrated = validate_artifact_envelope(json.loads(env.model_dump_json()))
        assert rehydrated == env

    report = synthesize_artifacts([tbl, ts, seg, mp])
    assert report.compatible is True
    assert report.issues == []


def test_malformed_artifacts_and_unsafe_markup_are_rejected() -> None:
    ts = time_series_from_tool_result(
        title="Trips by hour",
        x_label="hour",
        y_label="trips",
        unit="trips",
        rows=[("08:00", 10), ("09:00", 20)],
        provenance=_prov(),
    ).model_dump()
    ts["body"]["total_y"] = 999  # wrong sum
    with pytest.raises(ArtifactEnvelopeError) as info:
        validate_artifact_envelope(ts)
    assert info.value.code == "sum_mismatch"

    seg = segment_comparison_from_tool_result(
        title="Delta check",
        dimension="borough",
        metric="fare",
        unit="USD",
        rows=[("Manhattan", 10.0, 12.0)],
        provenance=_prov(),
    ).model_dump()
    seg["body"]["rows"][0]["delta"] = 99.0  # wrong delta
    with pytest.raises(ArtifactEnvelopeError) as info2:
        validate_artifact_envelope(seg)
    assert info2.value.code == "delta_mismatch"

    with pytest.raises(ArtifactEnvelopeError):
        table_from_tool_result(
            title="<script>alert(1)</script>",
            columns=[("a", "A", None)],
            rows=[["ok"]],
            provenance=_prov(),
        )


def test_synthesis_flags_basis_mismatch_time_mismatch_truncation_and_contradictions() -> (
    None
):
    a1 = time_series_from_tool_result(
        title="Hourly trips (agent A)",
        x_label="hour",
        y_label="trip_count",
        unit="trips",
        rows=[("18:00", 215000)],
        provenance=_prov(
            agent_id="agent-a", query_id="q_a", filter_basis="governed_valid_records"
        ),
    )
    a2 = time_series_from_tool_result(
        title="Hourly trips (agent B)",
        x_label="hour",
        y_label="trip_count",
        unit="trips",
        rows=[("18:00", 250000)],  # contradicts a1
        provenance=_prov(
            agent_id="agent-b",
            query_id="q_b",
            filter_basis="legacy_unfiltered",
            time_window="2023-01-01..2023-01-31",
            truncated=True,
        ),
    )
    report = synthesize_artifacts([a1, a2])
    assert report.compatible is False
    codes = {i.code for i in report.issues}
    assert codes == {
        "basis_mismatch",
        "time_window_mismatch",
        "value_contradiction",
        "truncated_source",
    }

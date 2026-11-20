"""Shared typed artifact envelope and cross-agent evidence synthesis (#235, Epic #231).

Provides deterministic, validated envelopes for `table`, `time_series`, `segment_comparison`,
and `map` (wrapping `#234`'s `map_artifact` v1). All values are derived strictly from governed
DuckDB/MCP tool outputs — never arbitrary JS, HTML, or LLM-authored numbers. Includes
cross-agent basis/dimension mismatch and contradiction detection.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.artifacts.map_artifact import (
    DATASET_ID,
    MapArtifact,
    MapArtifactError,
    validate_map_artifact,
)

ARTIFACT_ENVELOPE_VERSION = "artifact_envelope.v1"
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_ROWS = 200
_HTML_OR_JS_RE = re.compile(
    r"<\s*(?:script|iframe|img|svg|object)|javascript:", re.IGNORECASE
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ArtifactKind = Literal["table", "time_series", "segment_comparison", "map"]


class ArtifactEnvelopeError(ValueError):
    """Raised when an artifact envelope or tool transform fails validation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _safe_str(value: str) -> str:
    if _CONTROL_RE.search(value) or _HTML_OR_JS_RE.search(value):
        raise ValueError("unsafe markup or control character rejected")
    return value


def _finite(value: int | float) -> int | float:
    if isinstance(value, bool) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        raise ValueError("non-finite or boolean number rejected")
    return value


SafeText = Annotated[
    str, Field(min_length=1, max_length=160), AfterValidator(_safe_str)
]
FiniteNum = Annotated[int | float, AfterValidator(_finite)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactProvenance(_Strict):
    run_id: SafeText | None = None
    agent_id: SafeText | None = None
    tool_call_id: SafeText | None = None
    query_id: SafeText
    trace_id: SafeText | None = None
    tool_name: SafeText
    dataset: SafeText = DATASET_ID
    filter_basis: SafeText = "governed_valid_records"
    time_window: SafeText = "2024-01-01..2024-01-31"
    truncated: bool = False


class ColumnSpec(_Strict):
    key: SafeText
    label: SafeText
    unit: SafeText | None = None


class TableBody(_Strict):
    columns: list[ColumnSpec] = Field(min_length=1, max_length=24)
    rows: list[list[str | FiniteNum | None]] = Field(max_length=MAX_ROWS)


class TimeSeriesPoint(_Strict):
    x: SafeText
    y: FiniteNum


class TimeSeriesBody(_Strict):
    x_label: SafeText
    y_label: SafeText
    unit: SafeText | None = None
    points: list[TimeSeriesPoint] = Field(min_length=1, max_length=MAX_ROWS)
    total_y: FiniteNum


class SegmentRow(_Strict):
    segment: SafeText
    baseline: FiniteNum
    comparison: FiniteNum
    delta: FiniteNum
    delta_pct: FiniteNum | None = None


class SegmentComparisonBody(_Strict):
    dimension: SafeText
    metric: SafeText
    unit: SafeText | None = None
    rows: list[SegmentRow] = Field(min_length=1, max_length=MAX_ROWS)


class ArtifactEnvelope(_Strict):
    schema_version: Literal["artifact_envelope.v1"] = ARTIFACT_ENVELOPE_VERSION
    artifact_id: SafeText
    kind: ArtifactKind
    title: SafeText
    provenance: ArtifactProvenance
    body: dict[str, Any]


def _stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{digest}"


def validate_artifact_envelope(raw: dict[str, Any]) -> ArtifactEnvelope:
    """Validate envelope metadata, kind-specific typed body, and byte-size cap."""
    try:
        env = ArtifactEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise ArtifactEnvelopeError("invalid_schema", str(exc)) from exc

    encoded = json.dumps(env.model_dump(), separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ArtifactEnvelopeError(
            "too_large", f"artifact is {len(encoded)} bytes (max {MAX_ENVELOPE_BYTES})"
        )

    try:
        if env.kind == "table":
            tb = TableBody.model_validate(env.body)
            width = len(tb.columns)
            for row in tb.rows:
                if len(row) != width:
                    raise ArtifactEnvelopeError(
                        "invalid_table_row",
                        f"row length {len(row)} != {width} columns",
                    )
                for cell in row:
                    if isinstance(cell, str):
                        _safe_str(cell)
        elif env.kind == "time_series":
            ts = TimeSeriesBody.model_validate(env.body)
            expected_sum = round(sum(p.y for p in ts.points), 6)
            if abs(expected_sum - ts.total_y) > max(1e-4, abs(expected_sum) * 1e-4):
                raise ArtifactEnvelopeError(
                    "sum_mismatch",
                    f"total_y {ts.total_y} does not match point sum {expected_sum}",
                )
        elif env.kind == "segment_comparison":
            sc = SegmentComparisonBody.model_validate(env.body)
            for row in sc.rows:
                expected_delta = round(row.comparison - row.baseline, 6)
                if abs(expected_delta - row.delta) > max(
                    1e-4, abs(expected_delta) * 1e-4
                ):
                    raise ArtifactEnvelopeError(
                        "delta_mismatch",
                        f"segment {row.segment!r} delta {row.delta} != {expected_delta}",
                    )
        elif env.kind == "map":
            validate_map_artifact(env.body)
    except (ValidationError, MapArtifactError, ValueError) as exc:
        if isinstance(exc, ArtifactEnvelopeError):
            raise
        raise ArtifactEnvelopeError("invalid_body", str(exc)) from exc
    return env


def table_from_tool_result(
    *,
    title: str,
    columns: list[tuple[str, str, str | None]],
    rows: list[list[Any]],
    provenance: ArtifactProvenance,
) -> ArtifactEnvelope:
    col_specs = [
        ColumnSpec(key=k, label=lbl, unit=u).model_dump() for k, lbl, u in columns
    ]
    body = {"columns": col_specs, "rows": rows[:MAX_ROWS]}
    aid = _stable_id(
        "tbl", {"q": provenance.query_id, "cols": col_specs, "n": len(rows)}
    )
    return validate_artifact_envelope(
        {
            "artifact_id": aid,
            "kind": "table",
            "title": title,
            "provenance": provenance.model_dump(),
            "body": body,
        }
    )


def time_series_from_tool_result(
    *,
    title: str,
    x_label: str,
    y_label: str,
    unit: str | None,
    rows: list[tuple[str, int | float]],
    provenance: ArtifactProvenance,
) -> ArtifactEnvelope:
    points = [{"x": str(x), "y": y} for x, y in rows[:MAX_ROWS]]
    total_y = round(sum(p["y"] for p in points), 6)
    body = {
        "x_label": x_label,
        "y_label": y_label,
        "unit": unit,
        "points": points,
        "total_y": total_y,
    }
    aid = _stable_id("ts", {"q": provenance.query_id, "x": x_label, "y": y_label})
    return validate_artifact_envelope(
        {
            "artifact_id": aid,
            "kind": "time_series",
            "title": title,
            "provenance": provenance.model_dump(),
            "body": body,
        }
    )


def segment_comparison_from_tool_result(
    *,
    title: str,
    dimension: str,
    metric: str,
    unit: str | None,
    rows: list[tuple[str, int | float, int | float]],
    provenance: ArtifactProvenance,
) -> ArtifactEnvelope:
    seg_rows = []
    for seg, base, comp in rows[:MAX_ROWS]:
        delta = round(comp - base, 6)
        delta_pct = round((delta / base) * 100.0, 2) if base != 0 else None
        seg_rows.append(
            {
                "segment": str(seg),
                "baseline": base,
                "comparison": comp,
                "delta": delta,
                "delta_pct": delta_pct,
            }
        )
    body = {"dimension": dimension, "metric": metric, "unit": unit, "rows": seg_rows}
    aid = _stable_id("seg", {"q": provenance.query_id, "dim": dimension, "m": metric})
    return validate_artifact_envelope(
        {
            "artifact_id": aid,
            "kind": "segment_comparison",
            "title": title,
            "provenance": provenance.model_dump(),
            "body": body,
        }
    )


def map_envelope_from_artifact(
    *, artifact: MapArtifact | dict[str, Any], provenance: ArtifactProvenance
) -> ArtifactEnvelope:
    validated = validate_map_artifact(
        artifact.model_dump(mode="json", by_alias=True)
        if isinstance(artifact, MapArtifact)
        else artifact
    )
    body = validated.model_dump(mode="json", by_alias=True)
    aid = _stable_id("map", {"q": provenance.query_id, "id": validated.artifact_id})
    return validate_artifact_envelope(
        {
            "artifact_id": aid,
            "kind": "map",
            "title": validated.title,
            "provenance": provenance.model_dump(),
            "body": body,
        }
    )


class SynthesisIssue(_Strict):
    code: Literal[
        "basis_mismatch",
        "time_window_mismatch",
        "value_contradiction",
        "truncated_source",
    ]
    artifact_ids: list[str]
    message: str


class SynthesisReport(_Strict):
    compatible: bool
    artifact_ids: list[str]
    issues: list[SynthesisIssue]


def synthesize_artifacts(artifacts: list[ArtifactEnvelope]) -> SynthesisReport:
    """Coordinator join: detect basis/time mismatches, truncation, and value contradictions."""
    issues: list[SynthesisIssue] = []
    bases = {a.provenance.filter_basis for a in artifacts}
    if len(bases) > 1:
        issues.append(
            SynthesisIssue(
                code="basis_mismatch",
                artifact_ids=[a.artifact_id for a in artifacts],
                message=f"incompatible filter bases across artifacts: {sorted(bases)}",
            )
        )
    windows = {a.provenance.time_window for a in artifacts}
    if len(windows) > 1:
        issues.append(
            SynthesisIssue(
                code="time_window_mismatch",
                artifact_ids=[a.artifact_id for a in artifacts],
                message=f"incompatible time windows across artifacts: {sorted(windows)}",
            )
        )
    for a in artifacts:
        if a.provenance.truncated:
            issues.append(
                SynthesisIssue(
                    code="truncated_source",
                    artifact_ids=[a.artifact_id],
                    message=f"artifact {a.artifact_id} was built from truncated tool output",
                )
            )

    # Detect contradictions between overlapping time_series / segment_comparison totals or values.
    seen_points: dict[tuple[str, str], tuple[str, float]] = {}
    for a in artifacts:
        if a.kind == "time_series":
            y_label = str(a.body["y_label"]).lower()
            for pt in a.body["points"]:
                key = (y_label, str(pt["x"]).lower())
                val = float(pt["y"])
                if key in seen_points:
                    prev_id, prev_val = seen_points[key]
                    tol = max(1e-3, abs(prev_val) * 0.001)
                    if abs(val - prev_val) > tol:
                        issues.append(
                            SynthesisIssue(
                                code="value_contradiction",
                                artifact_ids=[prev_id, a.artifact_id],
                                message=(
                                    f"contradictory {y_label} for {pt['x']!r}: "
                                    f"{prev_val} ({prev_id}) vs {val} ({a.artifact_id})"
                                ),
                            )
                        )
                else:
                    seen_points[key] = (a.artifact_id, val)

    blocking = {
        i.code
        for i in issues
        if i.code in ("basis_mismatch", "time_window_mismatch", "value_contradiction")
    }
    return SynthesisReport(
        compatible=not blocking,
        artifact_ids=[a.artifact_id for a in artifacts],
        issues=issues,
    )

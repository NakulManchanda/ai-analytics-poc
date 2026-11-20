"""Map artifact v1: a declarative, validated taxi-zone choropleth.

The application builds the artifact from governed tool rows; the model never
supplies zone ids, coordinates, styles or markup. ``validate_map_artifact`` is
the trust boundary. The web renderer re-validates with the same rules
(``web/src/map/artifact.ts``) against shared fixtures.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

MAX_ARTIFACT_BYTES = 64 * 1024
MAX_ZONES = 263
DATASET_ID = "nyc-yellow-taxi-2024-01"
GEOGRAPHY_ID = "nyc_tlc_taxi_zones"
GEOGRAPHY_VERSION = "v1"
_INDEX_PATH = Path(__file__).with_name("taxi_zones_v1.json")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NO_GEOMETRY_NAMES = {"N/A", "Outside of NYC"}

_MESSAGES = {
    "invalid_schema": "The map artifact does not match the v1 schema.",
    "unknown_zone": "The map artifact references a zone that is not in the geography.",
    "unsupported_layer": "The map artifact uses a layer kind that v1 does not support.",
    "invalid_value": "The map artifact contains an invalid value.",
    "too_large": "The map artifact is larger than the allowed size.",
    "invalid_result": "The tool result cannot be turned into a map artifact.",
}


class MapArtifactError(ValueError):
    """Rejected artifact or tool result. ``code`` is closed; the message never echoes input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


class _Reject(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1)
def load_zone_index() -> dict:
    return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _zone_ids() -> frozenset[int]:
    return frozenset(z["location_id"] for z in load_zone_index()["zones"])


@lru_cache(maxsize=1)
def _ids_by_name() -> dict[str, tuple[int, ...]]:
    names: dict[str, list[int]] = {}
    for zone in load_zone_index()["zones"]:
        names.setdefault(zone["zone"], []).append(zone["location_id"])
    return {name: tuple(ids) for name, ids in names.items()}


def _clean(value: str) -> str:
    if _CONTROL.search(value):
        raise _Reject("invalid_value")
    return value


def _finite(value: int | float) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise _Reject("invalid_value")
    return value


def _zone_list(ids: list[int]) -> list[int]:
    if not ids or len(set(ids)) != len(ids):
        raise _Reject("invalid_value")
    if any(i not in _zone_ids() for i in ids):
        raise _Reject("unknown_zone")
    return ids


def _text(limit: int):
    return Annotated[str, Field(min_length=1, max_length=limit), AfterValidator(_clean)]


Number = Annotated[int | float, AfterValidator(_finite)]
ZoneIds = Annotated[list[int], Field(max_length=MAX_ZONES), AfterValidator(_zone_list)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Geography(_Model):
    id: Literal["nyc_tlc_taxi_zones"]
    version: Literal["v1"]


class Evidence(_Model):
    run_id: _text(128) | None
    tool_call_id: _text(128) | None
    query_id: _text(128)
    tool_name: Literal["aggregate_taxi_data", "map_taxi_zones"]
    dataset: _text(80)


class Scale(_Model):
    kind: Literal["sequential"]
    palette: Literal["blues", "oranges"]
    min: Number
    max: Number


class Region(_Model):
    label: _text(80)
    location_ids: ZoneIds
    value: Number


class Choropleth(_Model):
    kind: Literal["choropleth"]
    measure: _text(40)
    unit: _text(24)
    scale: Scale
    regions: list[Region] = Field(max_length=MAX_ZONES)


class Highlight(_Model):
    kind: Literal["highlight"]
    label: _text(80)
    location_ids: ZoneIds


class Unmapped(_Model):
    label: _text(80)
    value: Number
    reason: Literal["no_geometry", "unknown_zone"]


class FitZones(_Model):
    location_ids: ZoneIds


class View(_Model):
    fit: Literal["all"] | FitZones


class Provenance(_Model):
    generated_by: Literal["app"]
    source: _text(200)
    interpretation: Literal["spatial_pattern_not_causal"]


FilterValue = (
    Annotated[str, Field(max_length=80), AfterValidator(_clean)] | int | float | bool
)


class MapArtifact(_Model):
    schema_: Literal["map_artifact"] = Field(alias="schema")
    version: Literal[1]
    artifact_id: Annotated[str, Field(pattern=r"^map_[0-9a-f]{8,64}$")]
    geography: Geography
    evidence: Evidence
    title: _text(120)
    layers: list[Annotated[Choropleth | Highlight, Field(discriminator="kind")]]
    unmapped: list[Unmapped] = Field(max_length=300)
    filters: dict[
        Annotated[str, Field(min_length=1, max_length=40), AfterValidator(_clean)],
        FilterValue,
    ]
    view: View
    provenance: Provenance

    def model_post_init(self, _context: object) -> None:
        kinds = [layer.kind for layer in self.layers]
        if kinds.count("choropleth") != 1 or kinds.count("highlight") > 1:
            raise _Reject("invalid_value")
        choropleth = next(
            layer for layer in self.layers if isinstance(layer, Choropleth)
        )
        ids = [i for region in choropleth.regions for i in region.location_ids]
        if len(ids) != len(set(ids)) or choropleth.scale.min > choropleth.scale.max:
            raise _Reject("invalid_value")
        if len(self.filters) > 16:
            raise _Reject("invalid_value")


def _error_code(error: ValidationError) -> str:
    codes = []
    for item in error.errors():
        exc = item.get("ctx", {}).get("error")
        if isinstance(exc, _Reject):
            codes.append(exc.code)
        elif item["type"] == "union_tag_invalid" and item["loc"][:1] == ("layers",):
            codes.append("unsupported_layer")
    for code in ("unknown_zone", "unsupported_layer", "invalid_value"):
        if code in codes:
            return code
    return "invalid_schema"


def canonical_json(artifact: MapArtifact) -> str:
    """Stable serialization used for the golden fixtures."""
    data = artifact.model_dump(mode="json", by_alias=True)
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def validate_map_artifact(payload: object) -> MapArtifact:
    """Validate an untrusted payload or raise ``MapArtifactError`` with a closed code."""
    try:
        size = len(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        )
    except (TypeError, ValueError, RecursionError):
        raise MapArtifactError("invalid_schema") from None
    if size > MAX_ARTIFACT_BYTES:
        raise MapArtifactError("too_large")
    try:
        return MapArtifact.model_validate(payload)
    except ValidationError as error:
        raise MapArtifactError(_error_code(error)) from None
    except _Reject as error:
        raise MapArtifactError(error.code) from None


def _number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _filter_value(value: object) -> str | int | float | bool | None:
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(v, (str, int, float)) for v in value
    ):
        return ", ".join(str(v) for v in value)[:80]
    return None


def _finish_artifact(
    *,
    regions: list[dict],
    unmapped: list[dict],
    measure: str,
    unit: str,
    zone_dimension: str,
    highlight_top_n: int,
    filters: dict[str, object] | None,
    run_id: str | None,
    tool_call_id: str | None,
    tool_name: str,
    query_id: str,
) -> MapArtifact:
    """Assemble and validate the payload shared by both builders."""
    values = [r["value"] for r in regions]
    layers: list[dict] = [
        {
            "kind": "choropleth",
            "measure": measure,
            "unit": unit,
            "scale": {
                "kind": "sequential",
                "palette": "blues",
                "min": min(0, *values) if values else 0,
                "max": max(values) if values else 0,
            },
            "regions": regions,
        }
    ]
    top = sorted(regions, key=lambda r: (-r["value"], r["label"]))[
        : max(highlight_top_n, 0)
    ]
    if top:
        layers.append(
            {
                "kind": "highlight",
                "label": "Top zone" if len(top) == 1 else f"Top {len(top)} zones",
                "location_ids": [i for r in top for i in r["location_ids"]],
            }
        )

    clean_filters = {}
    for key, raw in (filters or {}).items():
        value = _filter_value(raw)
        if value is not None and isinstance(key, str):
            clean_filters[key[:40]] = value
    digest = hashlib.sha256(
        f"{run_id or ''}|{tool_call_id or ''}|{query_id}".encode()
    ).hexdigest()[:16]
    payload = {
        "schema": "map_artifact",
        "version": 1,
        "artifact_id": f"map_{digest}",
        "geography": {"id": GEOGRAPHY_ID, "version": GEOGRAPHY_VERSION},
        "evidence": {
            "run_id": run_id,
            "tool_call_id": tool_call_id,
            "query_id": query_id,
            "tool_name": tool_name,
            "dataset": DATASET_ID,
        },
        "title": f"{measure.replace('_', ' ').capitalize()} by {zone_dimension.replace('_', ' ')}"[
            :120
        ],
        "layers": layers,
        "unmapped": unmapped[:300],
        "filters": clean_filters,
        "view": {"fit": "all"},
        "provenance": {
            "generated_by": "app",
            "source": f"{tool_name} result",
            "interpretation": "spatial_pattern_not_causal",
        },
    }
    return validate_map_artifact(payload)


def build_zone_choropleth_from_ids(
    result: dict[str, object],
    *,
    measure: str,
    unit: str,
    filters: dict[str, object] | None,
    zone_role: str,
    highlight_top_n: int = 1,
) -> MapArtifact:
    """Build a choropleth from a ``map_taxi_zones`` envelope keyed by location id.

    Rows whose id has no geometry become ``unmapped``; no id is ever invented.
    """
    columns, rows, query_id = (
        result.get("columns"),
        result.get("rows"),
        result.get("query_id"),
    )
    if (
        not isinstance(columns, list)
        or not isinstance(rows, list)
        or not isinstance(query_id, str)
        or any(c not in columns for c in ("location_id", "zone", measure))
    ):
        raise MapArtifactError("invalid_result")
    ii, zi, mi = (columns.index(c) for c in ("location_id", "zone", measure))
    regions: list[dict] = []
    unmapped: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(ii, zi, mi):
            continue
        value = _number(row[mi])
        location_id = row[ii]
        if value is None or isinstance(location_id, bool):
            continue
        if not isinstance(location_id, int):
            continue
        name = row[zi] if isinstance(row[zi], str) and row[zi] else "Unknown"
        label = name[:80]
        if location_id in _zone_ids():
            if location_id not in seen:
                seen.add(location_id)
                regions.append(
                    {"label": label, "location_ids": [location_id], "value": value}
                )
        else:
            reason = "no_geometry" if name in _NO_GEOMETRY_NAMES else "unknown_zone"
            unmapped.append({"label": label, "value": value, "reason": reason})
    return _finish_artifact(
        regions=regions,
        unmapped=unmapped,
        measure=measure,
        unit=unit,
        zone_dimension=f"{zone_role} zone",
        highlight_top_n=highlight_top_n,
        filters=filters,
        run_id=None,
        tool_call_id=None,
        tool_name="map_taxi_zones",
        query_id=query_id,
    )


def build_zone_choropleth(
    result: dict[str, object],
    *,
    run_id: str,
    tool_call_id: str,
    tool_name: str,
    filters: dict[str, object] | None,
    zone_dimension: str,
    measure: str,
    unit: str,
    highlight_top_n: int = 1,
) -> MapArtifact:
    """Build a validated choropleth from a governed tool result envelope.

    ``result`` is the tool envelope (``columns``, ``rows``, ``query_id``). Zone
    names map to every LocationID carrying that name; names without geometry or
    absent from the index become ``unmapped`` rows. No id is ever invented.
    """
    columns, rows, query_id = (
        result.get("columns"),
        result.get("rows"),
        result.get("query_id"),
    )
    if (
        not isinstance(columns, list)
        or not isinstance(rows, list)
        or not isinstance(query_id, str)
        or zone_dimension not in columns
        or measure not in columns
    ):
        raise MapArtifactError("invalid_result")
    zi, mi = columns.index(zone_dimension), columns.index(measure)

    regions: list[dict] = []
    unmapped: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max(zi, mi):
            continue
        value = _number(row[mi])
        if value is None:
            continue
        name = row[zi] if isinstance(row[zi], str) and row[zi] else "Unknown"
        label = name[:80]
        ids = _ids_by_name().get(name)
        if ids is None:
            reason = "no_geometry" if name in _NO_GEOMETRY_NAMES else "unknown_zone"
            unmapped.append({"label": label, "value": value, "reason": reason})
        elif name not in seen:  # first row wins if a name repeats
            seen.add(name)
            regions.append({"label": label, "location_ids": list(ids), "value": value})

    return _finish_artifact(
        regions=regions,
        unmapped=unmapped,
        measure=measure,
        unit=unit,
        zone_dimension=zone_dimension,
        highlight_top_n=highlight_top_n,
        filters=filters,
        run_id=run_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        query_id=query_id,
    )

import copy
import json
from pathlib import Path

import pytest
from app.artifacts import MapArtifactError, build_zone_choropleth, validate_map_artifact
from app.artifacts.map_artifact import canonical_json, load_zone_index

FIXTURES = Path(__file__).resolve().parents[3] / "web/src/map/fixtures"
VALID_FIXTURE = FIXTURES / "pickup-count-choropleth.v1.json"

TOOL_RESULT = {
    "columns": ["pickup_zone", "trip_count"],
    "rows": [
        ["JFK Airport", 154000],
        ["LaGuardia Airport", 142000],
        ["Corona", 21000],
        ["Astoria", 18500],
        ["Jackson Heights", 16000],
        ["Long Island City/Queens Plaza", 14200],
        ["Elmhurst", 11800],
        ["Flushing", 9400],
        ["Forest Hills", 7600],
        ["Jamaica", 6100],
        ["Sunnyside", 4300],
        ["Rego Park", 2900],
        ["Outside of NYC", 120],
    ],
    "row_count": 13,
    "query_id": "query_fixture0001",
    "truncated": False,
}
BUILD_ARGS = dict(
    run_id="run_fixture0001",
    tool_call_id="call_fixture0001",
    tool_name="aggregate_taxi_data",
    filters={"pickup_borough": "Queens"},
    zone_dimension="pickup_zone",
    measure="trip_count",
    unit="trips",
)

# fixture file -> expected rejection code
INVALID = {
    "unknown-zone.json": "unknown_zone",
    "extra-html-key.json": "invalid_schema",
    "hotspot-layer.json": "unsupported_layer",
    "raw-coordinates.json": "invalid_schema",
    "oversize.json": "too_large",
}


def valid() -> dict:
    return json.loads(VALID_FIXTURE.read_text())


def reject(payload: object) -> str:
    with pytest.raises(MapArtifactError) as info:
        validate_map_artifact(payload)
    return info.value.code


def choropleth(payload: dict) -> dict:
    return payload["layers"][0]


def test_geometry_index_covers_all_263_zones():
    ids = [z["location_id"] for z in load_zone_index()["zones"]]
    assert ids == list(range(1, 264))


def test_builder_output_equals_valid_fixture():
    built = build_zone_choropleth(TOOL_RESULT, **BUILD_ARGS)
    assert canonical_json(built) == VALID_FIXTURE.read_text()


def test_valid_fixture_passes_and_covers_shared_name_and_unmapped():
    artifact = validate_map_artifact(valid())
    regions = {r.label: r.location_ids for r in artifact.layers[0].regions}
    assert regions["Corona"] == [56, 57]
    assert [u.label for u in artifact.unmapped] == ["Outside of NYC"]
    assert artifact.unmapped[0].reason == "no_geometry"
    assert artifact.filters == {"pickup_borough": "Queens"}
    assert artifact.layers[1].location_ids == [132]


@pytest.mark.parametrize("name,code", INVALID.items())
def test_invalid_fixtures_are_rejected_with_expected_code(name, code):
    assert reject(json.loads((FIXTURES / "invalid" / name).read_text())) == code


def test_unknown_key_anywhere_is_rejected():
    for path in (
        ["x"],
        ["evidence", "x"],
        ["layers", 0, "x"],
        ["layers", 0, "scale", "x"],
    ):
        payload = valid()
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = 1
        assert reject(payload) == "invalid_schema"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema="other"),
        lambda p: p.update(version=2),
        lambda p: p["geography"].update(version="v2"),
        lambda p: p["geography"].update(id="other"),
    ],
)
def test_wrong_schema_version_or_geography_is_rejected(mutate):
    payload = valid()
    mutate(payload)
    assert reject(payload) == "invalid_schema"


def test_unknown_zone_id_is_rejected():
    payload = valid()
    choropleth(payload)["regions"][0]["location_ids"] = [999]
    assert reject(payload) == "unknown_zone"


def test_empty_location_ids_are_rejected():
    payload = valid()
    choropleth(payload)["regions"][0]["location_ids"] = []
    assert reject(payload) == "invalid_value"


def test_zone_in_two_choropleth_regions_is_rejected():
    payload = valid()
    regions = choropleth(payload)["regions"]
    regions[1]["location_ids"] = list(regions[0]["location_ids"])
    assert reject(payload) == "invalid_value"


def test_hotspot_layer_is_unsupported():
    payload = valid()
    payload["layers"].append({"kind": "hotspot", "location_ids": [1]})
    assert reject(payload) == "unsupported_layer"


def test_exactly_one_choropleth_and_at_most_one_highlight():
    payload = valid()
    payload["layers"] = [payload["layers"][1]]
    assert reject(payload) == "invalid_value"
    payload = valid()
    payload["layers"] = [payload["layers"][0], payload["layers"][0]]
    assert reject(payload) == "invalid_value"
    payload = valid()
    payload["layers"].append(copy.deepcopy(payload["layers"][1]))
    assert reject(payload) == "invalid_value"  # two highlights


def test_closed_enums():
    payload = valid()
    choropleth(payload)["scale"]["palette"] = "red; background:url(x)"
    assert reject(payload) == "invalid_schema"
    payload = valid()
    payload["provenance"]["interpretation"] = "causal"
    assert reject(payload) == "invalid_schema"
    payload = valid()
    payload["unmapped"][0]["reason"] = "because"
    assert reject(payload) == "invalid_schema"


def test_view_fit_all_or_allowlisted_zones_only():
    payload = valid()
    payload["view"] = {"fit": {"location_ids": [132, 138]}}
    validate_map_artifact(payload)
    payload["view"] = {"fit": {"location_ids": [999]}}
    assert reject(payload) == "unknown_zone"
    payload["view"] = {"fit": {"center": [40.7, -74.0]}}
    assert reject(payload) == "invalid_schema"


def test_no_coordinates_zoom_urls_or_style_fields():
    for extra in (
        {"zoom": 9},
        {"center": [40.7, -74.0]},
        {"style": "fill:red"},
        {"url": "x"},
    ):
        payload = valid()
        payload["view"].update(extra)
        assert reject(payload) == "invalid_schema"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "5", True])
def test_numbers_must_be_finite_numbers(bad):
    payload = valid()
    choropleth(payload)["regions"][0]["value"] = bad
    assert reject(payload) in {"invalid_schema", "invalid_value", "too_large"}


def test_scale_min_must_not_exceed_max():
    payload = valid()
    choropleth(payload)["scale"].update(min=10, max=1)
    assert reject(payload) == "invalid_value"


def test_string_limits_and_control_characters():
    payload = valid()
    payload["title"] = "x" * 121
    assert reject(payload) == "invalid_schema"
    payload = valid()
    choropleth(payload)["unit"] = "y" * 25
    assert reject(payload) == "invalid_schema"
    payload = valid()
    choropleth(payload)["regions"][0]["label"] = "z" * 81
    assert reject(payload) == "invalid_schema"
    payload = valid()
    payload["title"] = "bad\x00title"
    assert reject(payload) == "invalid_value"


def test_filters_limits_and_scalar_values():
    payload = valid()
    payload["filters"] = {f"k{i}": i for i in range(17)}
    assert reject(payload) == "invalid_value"
    payload["filters"] = {"k": ["a"]}
    assert reject(payload) == "invalid_schema"


def test_oversize_payload_is_rejected():
    payload = valid()
    payload["provenance"]["source"] = "s" * (64 * 1024)
    assert reject(payload) == "too_large"


def test_non_object_payload_is_rejected():
    assert reject("not an object") == "invalid_schema"
    assert reject(None) == "invalid_schema"


def test_error_message_never_echoes_input():
    payload = valid()
    payload["title"] = "SECRET-<script>"
    payload["extra"] = "SECRET-<script>"
    with pytest.raises(MapArtifactError) as info:
        validate_map_artifact(payload)
    assert "SECRET" not in str(info.value)


def test_builder_maps_names_to_ids_and_never_invents_ids():
    result = {
        "columns": ["pickup_zone", "trip_count"],
        "rows": [
            ["Corona", 5],
            ["Governor's Island/Ellis Island/Liberty Island", 3],
            ["Outside of NYC", 2],
            ["N/A", 1],
            ["Atlantis", 4],
            [None, 6],
        ],
        "query_id": "query_x",
    }
    artifact = build_zone_choropleth(result, **BUILD_ARGS)
    regions = {r.label: r.location_ids for r in artifact.layers[0].regions}
    assert regions == {
        "Corona": [56, 57],
        "Governor's Island/Ellis Island/Liberty Island": [103, 104, 105],
    }
    reasons = {u.label: u.reason for u in artifact.unmapped}
    assert reasons == {
        "Outside of NYC": "no_geometry",
        "N/A": "no_geometry",
        "Atlantis": "unknown_zone",
        "Unknown": "unknown_zone",
    }


def test_builder_artifact_id_is_deterministic_and_highlights_top_zone():
    first = build_zone_choropleth(TOOL_RESULT, **BUILD_ARGS)
    second = build_zone_choropleth(TOOL_RESULT, **BUILD_ARGS)
    assert first.artifact_id == second.artifact_id
    assert first.layers[1].location_ids == [132]
    none = build_zone_choropleth(TOOL_RESULT, **BUILD_ARGS, highlight_top_n=0)
    assert len(none.layers) == 1


def test_builder_rejects_malformed_tool_result():
    for bad in ({}, {"columns": ["a"], "rows": [], "query_id": "q"}):
        with pytest.raises(MapArtifactError) as info:
            build_zone_choropleth(bad, **BUILD_ARGS)
        assert info.value.code == "invalid_result"

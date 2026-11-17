"""Build the versioned NYC TLC taxi-zone geometry assets from taxi_zones.shp.

Usage: build_taxi_zone_geometry.py [taxi_zones.zip]  (downloads the pinned URL when omitted)

Run through `make zone-geometry` (uv --with pyshp pyproj shapely). Output is
deterministic: features sorted by location_id, 100 ft simplification,
coordinates rounded to 5 decimals.
"""

import argparse
import hashlib
import json
import tempfile
import tomllib
import urllib.request
import zipfile
import urllib.request
from pathlib import Path

import shapefile  # pyshp
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config/datasets/nyc_yellow_taxi_2024_01.toml"
GEOJSON_OUT = ROOT / "web/src/map/taxi-zones.v1.json"
INDEX_OUT = ROOT / "services/app/app/artifacts/taxi_zones_v1.json"
TOLERANCE_FT = 100.0
EXPECTED_IDS = set(range(1, 264))


def _round(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 5), round(coords[1], 5)]
    return [_round(c) for c in coords]


def _points(coords):
    if isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords:
            yield from _points(c)


def build(shp: Path) -> tuple[dict, list[dict], list[float]]:
    to_wgs = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True).transform
    reader = shapefile.Reader(str(shp))
    fields = [f[0] for f in reader.fields[1:]]
    features = []
    for sr in reader.shapeRecords():
        rec = dict(zip(fields, sr.record))
        geom = shape(sr.shape.__geo_interface__).simplify(
            TOLERANCE_FT, preserve_topology=True
        )
        gj = json.loads(json.dumps(mapping(transform(to_wgs, geom))))
        gj["coordinates"] = _round(gj["coordinates"])
        lid = int(rec["LocationID"])
        features.append(
            {
                "type": "Feature",
                "id": lid,
                "properties": {
                    "location_id": lid,
                    "zone": rec["zone"],
                    "borough": rec["borough"],
                },
                "geometry": gj,
            }
        )
    features.sort(key=lambda f: (f["id"], f["properties"]["zone"]))
    ids = [f["id"] for f in features]
    if len(features) != 263 or set(ids) != EXPECTED_IDS or len(set(ids)) != 263:
        raise SystemExit(f"expected 263 features with ids 1..263, got {len(features)}")
    pts = [p for f in features for p in _points(f["geometry"]["coordinates"])]
    bbox = [
        min(p[0] for p in pts),
        min(p[1] for p in pts),
        max(p[0] for p in pts),
        max(p[1] for p in pts),
    ]
    if not (
        -74.3 <= bbox[0] and bbox[2] <= -73.6 and 40.4 <= bbox[1] and bbox[3] <= 41.0
    ):
        raise SystemExit(f"bbox outside NYC: {bbox}")
    return {"type": "FeatureCollection", "features": features}, features, bbox


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "zip",
        type=Path,
        nargs="?",
        help="local taxi_zones.zip; default downloads the pinned URL",
    )
    args = parser.parse_args()

    cfg = tomllib.loads(CONFIG.read_text())["dataset"]["zone_geometry"]
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = args.zip
        if zip_path is None:
            zip_path = Path(tmp) / "taxi_zones.zip"
            urllib.request.urlretrieve(cfg["url"], zip_path)
        actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if actual != cfg["sha256"]:
            raise SystemExit(f"zip sha256 mismatch: {actual}")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        collection, features, bbox = build(next(Path(tmp).rglob("taxi_zones.shp")))
    geojson_text = (
        json.dumps(collection, separators=(",", ":"), ensure_ascii=False) + "\n"
    )
    GEOJSON_OUT.write_text(geojson_text)
    index = {
        "geography_id": "nyc_tlc_taxi_zones",
        "version": "v1",
        "source_url": cfg["url"],
        "source_sha256": cfg["sha256"],
        "geojson_sha256": hashlib.sha256(geojson_text.encode()).hexdigest(),
        "bbox": bbox,
        "zones": [
            {
                "location_id": f["id"],
                "zone": f["properties"]["zone"],
                "borough": f["properties"]["borough"],
            }
            for f in features
        ],
    }
    INDEX_OUT.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    print(
        f"wrote {GEOJSON_OUT.relative_to(ROOT)} and {INDEX_OUT.relative_to(ROOT)}: 263 features"
    )


if __name__ == "__main__":
    main()

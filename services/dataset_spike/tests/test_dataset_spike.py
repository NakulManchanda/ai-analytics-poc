from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def test_loads_the_pinned_official_dataset_metadata():
    from dataset_spike.config import load_dataset_config

    config = load_dataset_config(
        Path(__file__).parents[3] / "config/datasets/nyc_yellow_taxi_2024_01.toml"
    )

    assert config.parquet.url == (
        "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet"
    )
    assert config.parquet.expected_bytes == 49_961_641
    assert config.parquet.sha256 == "c4d59da7bbc8abaeeeb1727947ee93d9891a71acb42854bd80db1571b2030510"
    assert config.zone_lookup.expected_rows == 265


def test_download_reuses_a_valid_cached_file(tmp_path: Path):
    import dataset_spike.download as download

    payload = b"cached dataset"
    destination = tmp_path / "yellow.parquet"
    destination.write_bytes(payload)

    result = download.ensure_cached_file(
        url="https://example.invalid/yellow.parquet",
        destination=destination,
        expected_bytes=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert result == destination
    assert destination.read_bytes() == payload


def test_download_replaces_a_corrupt_cached_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import dataset_spike.download as download

    expected = b"verified dataset"
    destination = tmp_path / "yellow.parquet"
    destination.write_bytes(b"corrupt")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, _size: int) -> bytes:
            value, self.value = getattr(self, "value", expected), b""
            return value

    monkeypatch.setattr(download, "urlopen", lambda _request, timeout: Response())

    assert download.ensure_cached_file(
        url="https://example.invalid/yellow.parquet",
        destination=destination,
        expected_bytes=len(expected),
        expected_sha256=hashlib.sha256(expected).hexdigest(),
    ) == destination
    assert destination.read_bytes() == expected


def test_profile_uses_fixed_bounded_queries_and_zone_join(tmp_path: Path):
    import duckdb

    from dataset_spike.analytics import profile_dataset

    parquet = tmp_path / "yellow.parquet"
    zones = tmp_path / "zones.csv"
    connection = duckdb.connect()
    connection.execute(
        """
        COPY (
            SELECT * FROM (VALUES
                (TIMESTAMP '2024-01-02 09:00:00', 1, 12.5),
                (TIMESTAMP '2024-01-02 10:00:00', 1, 7.5),
                (TIMESTAMP '2024-01-03 09:00:00', 2, 5.0)
            ) AS trips(tpep_pickup_datetime, PULocationID, total_amount)
        ) TO ? (FORMAT PARQUET)
        """,
        [str(parquet)],
    )
    connection.close()
    zones.write_text("LocationID,Borough,Zone,service_zone\n1,Manhattan,Alpha,Boro Zone\n2,Bronx,Beta,Boro Zone\n")

    profile = profile_dataset(parquet, zones, top_n=2)

    assert profile.row_count == 3
    assert profile.duckdb_settings == {"threads": "1", "memory_limit": "512MB"}
    assert profile.daily_zone_rows == [
        {"pickup_date": "2024-01-02", "pickup_zone": "Alpha", "trip_count": 2, "total_amount": 20.0},
        {"pickup_date": "2024-01-03", "pickup_zone": "Beta", "trip_count": 1, "total_amount": 5.0},
    ]
    assert "tpep_pickup_datetime" in profile.schema_columns
    assert profile.timing_ms >= 0
    assert profile.rss_bytes > 0

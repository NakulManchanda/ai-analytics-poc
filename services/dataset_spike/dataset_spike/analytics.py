from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import resource
import sys
import time

import duckdb


@dataclass(frozen=True)
class DatasetProfile:
    row_count: int
    schema_columns: list[str]
    daily_zone_rows: list[dict[str, object]]
    duckdb_settings: dict[str, str]
    timing_ms: int
    rss_bytes: int


def _rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage if sys.platform == "darwin" else usage * 1024


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def profile_dataset(parquet_path: Path, zone_csv_path: Path, *, top_n: int = 10) -> DatasetProfile:
    """Run only fixed inspection queries; no caller-supplied SQL reaches DuckDB."""
    if not 1 <= top_n <= 100:
        raise ValueError("top_n must be between 1 and 100")
    started = time.perf_counter()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads = 1")
        connection.execute("SET memory_limit = '512MB'")
        connection.execute(
            f"CREATE VIEW trips AS SELECT * FROM read_parquet('{_sql_path(parquet_path)}')"
        )
        connection.execute(
            f"CREATE VIEW taxi_zones AS SELECT * FROM read_csv_auto('{_sql_path(zone_csv_path)}', header = true)"
        )
        schema_columns = [row[0] for row in connection.execute("DESCRIBE trips").fetchall()]
        row_count = connection.execute("SELECT count(*) FROM trips").fetchone()[0]
        rows = connection.execute(
            """
            SELECT
                CAST(t.tpep_pickup_datetime AS DATE)::VARCHAR AS pickup_date,
                z.Zone AS pickup_zone,
                count(*)::BIGINT AS trip_count,
                round(sum(t.total_amount), 2) AS total_amount
            FROM trips AS t
            JOIN taxi_zones AS z ON t.PULocationID = z.LocationID
            GROUP BY 1, 2
            ORDER BY trip_count DESC, pickup_date ASC, pickup_zone ASC
            LIMIT ?
            """,
            [top_n],
        ).fetchall()
    finally:
        connection.close()
    return DatasetProfile(
        row_count=row_count,
        schema_columns=schema_columns,
        daily_zone_rows=[
            {"pickup_date": row[0], "pickup_zone": row[1], "trip_count": row[2], "total_amount": row[3]}
            for row in rows
        ],
        duckdb_settings={"threads": "1", "memory_limit": "512MB"},
        timing_ms=round((time.perf_counter() - started) * 1000),
        rss_bytes=_rss_bytes(),
    )

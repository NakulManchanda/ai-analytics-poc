from __future__ import annotations

import json
import multiprocessing
import queue
import resource
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

MAX_QUERY_ROWS = 20
MAX_RESULT_BYTES = 8_192
DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
ALLOWED_ANALYSES = {
    "top_pickup_zones": """
        SELECT z.Zone AS pickup_zone, count(*)::BIGINT AS trip_count
        FROM trips AS t
        JOIN taxi_zones AS z ON t.PULocationID = z.LocationID
        GROUP BY 1
        ORDER BY trip_count DESC, pickup_zone ASC
        LIMIT ?
    """,
    "trip_volume_by_hour": """
        SELECT extract(hour FROM tpep_pickup_datetime)::INTEGER AS pickup_hour,
               count(*)::BIGINT AS trip_count
        FROM trips
        GROUP BY 1
        ORDER BY trip_count DESC, pickup_hour ASC
        LIMIT ?
    """,
    "average_distance_by_weekday": """
        SELECT dayname(tpep_pickup_datetime) AS pickup_weekday,
               round(avg(trip_distance), 2) AS average_trip_distance
        FROM trips
        WHERE trip_distance >= 0
        GROUP BY 1
        ORDER BY min(extract(isodow FROM tpep_pickup_datetime)), pickup_weekday ASC
        LIMIT ?
    """,
}


@dataclass(frozen=True)
class DatasetProfile:
    row_count: int
    zone_row_count: int
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


def _execute_governed_query(
    parquet_path: str,
    zone_csv_path: str,
    query: str,
    fetch_limit: int,
    output_queue: Any,
) -> None:
    """Run DuckDB in a process the parent can terminate at the hard deadline."""
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET threads = 1")
        connection.execute("SET memory_limit = '512MB'")
        connection.execute(
            "CREATE TEMP TABLE trips AS SELECT * FROM "
            f"read_parquet('{_sql_path(Path(parquet_path))}')"
        )
        connection.execute(
            "CREATE TEMP TABLE taxi_zones AS SELECT * FROM "
            f"read_csv_auto('{_sql_path(Path(zone_csv_path))}', header = true)"
        )
        connection.execute("SET enable_external_access = false")
        cursor = connection.execute(query, [fetch_limit])
        output_queue.put(
            (
                "ok",
                [description[0] for description in cursor.description],
                cursor.fetchall(),
            )
        )
    except BaseException:
        output_queue.put(("error",))
    finally:
        connection.close()


def query_dataset(
    parquet_path: Path,
    zone_csv_path: Path,
    *,
    analysis: str,
    limit: int = 5,
    max_result_bytes: int = MAX_RESULT_BYTES,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    query_id_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute one internally defined read-only analysis over pinned local inputs."""
    if analysis not in ALLOWED_ANALYSES:
        raise ValueError("analysis is not allowlisted")
    if isinstance(limit, bool) or not 1 <= limit <= MAX_QUERY_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
    if isinstance(max_result_bytes, bool) or max_result_bytes < 128:
        raise ValueError("max_result_bytes must be at least 128")
    if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    started = time.perf_counter()
    process_context = multiprocessing.get_context("spawn")
    output_queue = process_context.Queue(maxsize=1)
    worker = process_context.Process(
        target=_execute_governed_query,
        args=(
            str(parquet_path),
            str(zone_csv_path),
            ALLOWED_ANALYSES[analysis],
            limit + 1,
            output_queue,
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        worker.terminate()
        worker.join(0.5)
        if worker.is_alive():
            worker.kill()
            worker.join(0.5)
        output_queue.close()
        output_queue.join_thread()
        raise TimeoutError("governed query exceeded its execution deadline")
    try:
        outcome = output_queue.get(timeout=0.5)
    except queue.Empty as error:
        raise RuntimeError("governed query worker returned no result") from error
    finally:
        output_queue.close()
        output_queue.join_thread()
    if outcome[0] != "ok":
        raise RuntimeError("governed query execution failed")
    _, columns, fetched_rows = outcome

    truncated = len(fetched_rows) > limit
    rows = [list(row) for row in fetched_rows[:limit]]
    result: dict[str, object] = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "execution_duration_ms": round((time.perf_counter() - started) * 1000),
        "query_id": (query_id_factory or (lambda: f"query_{uuid.uuid4().hex}"))(),
        "truncated": truncated,
    }
    while len(json.dumps(result, separators=(",", ":")).encode()) > max_result_bytes:
        if not rows:
            raise ValueError("max_result_bytes is too small for the result envelope")
        rows.pop()
        result["row_count"] = len(rows)
        result["truncated"] = True
    return result


def profile_dataset(
    parquet_path: Path, zone_csv_path: Path, *, top_n: int = 10
) -> DatasetProfile:
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
            "CREATE VIEW taxi_zones AS SELECT * FROM "
            f"read_csv_auto('{_sql_path(zone_csv_path)}', header = true)"
        )
        schema_columns = [
            row[0] for row in connection.execute("DESCRIBE trips").fetchall()
        ]
        row_count = connection.execute("SELECT count(*) FROM trips").fetchone()[0]
        zone_row_count = connection.execute(
            "SELECT count(*) FROM taxi_zones"
        ).fetchone()[0]
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
        zone_row_count=zone_row_count,
        schema_columns=schema_columns,
        daily_zone_rows=[
            {
                "pickup_date": row[0],
                "pickup_zone": row[1],
                "trip_count": row[2],
                "total_amount": row[3],
            }
            for row in rows
        ],
        duckdb_settings={"threads": "1", "memory_limit": "512MB"},
        timing_ms=round((time.perf_counter() - started) * 1000),
        rss_bytes=_rss_bytes(),
    )

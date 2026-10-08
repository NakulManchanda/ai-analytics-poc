"""E5 prefix locality/mobility scenarios that do NOT need real KV transfer (#123).

The scenario JSON files under ``config/scenarios/e5_*.json`` are generated from this module
(``uv run --project services/app python -m app.benchmarks.e5_locality``) and a test asserts the
committed files match, so prefix sizes and forced-worker patterns cannot drift silently.

Every case uses a SYNTHETIC padded system prefix (a prefix-size treatment, not the taxi-agent
prefix). Each case gets a unique tag at the START of its prefix so its KV blocks cannot be
reused by a different case (block hashing is chained from the first token). Sizes are
gateway-style estimates (JSON chars // 4); the exact count is recorded per run in
``manifest.json`` (``system_prefix.exact_tokens``) and is what a result must cite.

Cases (worker_a -> worker_b are the gateway's worker ids, forced with ``x-force-worker``):

* ``e5_local_reuse_<size>``: A -> A. Populate on A, continue on A. Local reuse is *possible*;
  observed reuse (prefix-cache hits window counters, TTFT) must confirm it.
* ``e5_recompute_control_<size>``: A -> B with NO transfer. B must prefill the prefix itself.
  This is the E5 control the real-transfer treatment (#133, `make inference-kv-smoke`) is compared
  against at the SAME prefix sizes.
* ``e5_destination_hit_<size>``: B is warmed separately with the same prefix, then an
  A-origin continuation goes to B. B hits its OWN cache; no hop occurred.
* ``e5_local_eviction_4k``: A -> A after filler conversations force eviction pressure on A.
  Whether eviction actually happened is NOT known from the scenario; verify from vLLM evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SIZES = {"1k": 1024, "2k": 2048, "4k": 4096, "7k": 7000}
MAX_TOKENS = 32
WINDOW, MARGIN = 8192, 256  # worker --max-model-len and the estimate-error margin
FILLER_COUNT, FILLER_TOKENS = 10, 7000
EVICTION_SIZE = "4k"
EVICTION_DELAY_S = 45.0
_SYSTEM_WRAP = len(json.dumps([{"role": "system", "content": ""}]))
REAL_TAXI_SECTIONS = (
    (
        "Dataset: nyc-yellow-taxi (2024-01). Pinned Parquet file: "
        "yellow_tripdata_2024-01.parquet (expected rows: 2964624). Zone lookup: "
        "taxi_zone_lookup.csv (265 zones across Manhattan, Queens, Brooklyn, Bronx, "
        "Staten Island, EWR). Pinned columns and types: VendorID (int64), "
        "tpep_pickup_datetime (timestamp_ntz), tpep_dropoff_datetime (timestamp_ntz), "
        "passenger_count (int64), trip_distance (float64), RatecodeID (int64), "
        "store_and_fwd_flag (string), PULocationID (int64), DOLocationID (int64), "
        "payment_type (int64), fare_amount (float64), extra (float64), "
        "mta_tax (float64), tip_amount (float64), tolls_amount (float64), "
        "improvement_surcharge (float64), total_amount (float64), "
        "congestion_surcharge (float64), Airport_fee (float64)."
    ),
    (
        "Governed MCP Analytics Tools: "
        "1. describe_taxi_dataset(include_column_stats: bool) yields summary statistics and "
        "code tables. "
        "2. list_taxi_dimension_values(dimension, search, limit) yields distinct category values. "
        "3. query_taxi_data(analysis, limit) runs pre-compiled DuckDB aggregations. "
        "4. aggregate_taxi_data(dimensions, measures, filters, order_by, limit) runs custom "
        "group-by queries. "
        "Governance rule: All queries must stay read-only, execute against pinned parquet, and "
        "enforce limit <= 20."
    ),
    (
        "Retrieved DuckDB Observation [top_pickup_zones]: "
        "Row 1: PULocationID=132, Zone=JFK Airport, Borough=Queens, trip_count=154231, "
        "avg_fare=68.20, avg_tip=11.45. "
        "Row 2: PULocationID=236, Zone=Upper East Side North, Borough=Manhattan, "
        "trip_count=142109, avg_fare=13.50, avg_tip=2.80. "
        "Row 3: PULocationID=161, Zone=Midtown Center, Borough=Manhattan, trip_count=138912, "
        "avg_fare=14.10, avg_tip=3.05. "
        "Row 4: PULocationID=237, Zone=Upper East Side South, Borough=Manhattan, "
        "trip_count=135440, avg_fare=13.20, avg_tip=2.75. "
        "Row 5: PULocationID=186, Zone=Penn Station/Madison Sq West, Borough=Manhattan, "
        "trip_count=129885, avg_fare=15.00, avg_tip=3.15."
    ),
    (
        "Retrieved DuckDB Observation [trip_volume_by_hour]: "
        "Peak evening rush: 18:00 (187420 trips, avg_fare=18.52), 17:00 (179310 trips, "
        "avg_fare=18.10), 19:00 (171200 trips, avg_fare=17.90). "
        "Morning peak: 08:00 (145100 trips, avg_fare=16.80), 09:00 (139800 trips, "
        "avg_fare=17.10). "
        "Off-peak early morning lull: 04:00 (14200 trips, avg_fare=24.50), "
        "03:00 (18400 trips, avg_fare=22.15)."
    ),
    (
        "Retrieved DuckDB Observation [average_trip_metrics by borough]: "
        "Manhattan trips: avg_distance=2.31 miles, avg_duration=14.2 minutes, avg_fare=15.40, "
        "avg_tip=3.10. "
        "Queens trips: avg_distance=8.92 miles, avg_duration=28.5 minutes, avg_fare=38.20, "
        "avg_tip=7.40. "
        "Brooklyn trips: avg_distance=5.64 miles, avg_duration=22.1 minutes, avg_fare=26.10, "
        "avg_tip=4.80. "
        "Bronx trips: avg_distance=7.10 miles, avg_duration=25.0 minutes, avg_fare=31.50, "
        "avg_tip=3.20."
    ),
    (
        "Taxi Zone Dimension Lookup: "
        "Covering 265 zones across boroughs Manhattan, Queens, Brooklyn, Bronx, Staten Island, "
        "and EWR. "
        "Rate codes: 1=Standard rate, 2=JFK Airport flat rate, 3=Newark, 4=Nassau Westchester, "
        "5=Negotiated fare, 6=Group ride. "
        "Payment types: 1=Credit card (82.4 percent), 2=Cash (15.1 percent), 3=No charge, "
        "4=Dispute."
    ),
    (
        "Assistant Governance Policy: "
        "Every analytical answer must strictly ground claims in returned DuckDB tool observations "
        "without speculating. "
        "Row limits bound every query to keep context windows small and prevent context exhaustion."
    ),
)
QUESTIONS = (
    ("Which pickup zones have the most trips?", "query_taxi_data"),
    (
        "What are the peak hours and busiest times for taxi rides in NYC?",
        "query_taxi_data",
    ),
)
WARM_QUESTION = (
    "List the pickup boroughs present in the dataset",
    "list_taxi_dimension_values",
)


def estimated_tokens(text: str) -> int:
    """The gateway/guard heuristic for a system message holding ``text``."""
    return len(json.dumps([{"role": "system", "content": text}])) // 4


def synthetic_prefix(tag: str, target_tokens: int) -> str:
    """Plain-ASCII padded prefix whose gateway estimate is ``target_tokens``. ``tag`` leads the
    text so different cases never share a KV block chain. Content is built from real NYC TLC
    yellow taxi domain material (schema, tool contracts, retrieved DuckDB observations)."""
    want = target_tokens * 4 - _SYSTEM_WRAP
    out = (
        f"SYNTHETIC E5 PREFIX case {tag} size {target_tokens}. "
        "[REAL DOMAIN MATERIAL: NYC TLC Yellow Taxi schema, tool contracts, "
        "and retrieved observations] "
    )
    n = 0
    while len(out) < want:
        out += f"Record {n}. {REAL_TAXI_SECTIONS[n % len(REAL_TAXI_SECTIONS)]} "
        n += 1
    return out[:want]


def _turn(q: tuple[str, str], worker: str, **kw: Any) -> dict[str, Any]:
    return {
        "question": q[0],
        "expected_tool": q[1],
        "delay_seconds": kw.pop("delay_seconds", 0.0),
        "force_worker": worker,
        **kw,
    }


def _scenario(
    name: str,
    treatment: str,
    description: str,
    prefix: str,
    convs: list,
    concurrency: int = 1,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description
        + " SYNTHETIC prefix-size treatment, not the taxi-agent prefix. Padding built from real "
        "NYC TLC yellow taxi domain material (dataset schema, tool contracts, retrieved "
        "DuckDB observations). Needs the gateway with ALLOW_FORCED_PLACEMENT=1 and no KV "
        "transfer backend. Real-transfer (#133) treatment is NOT implemented; it must reuse "
        "these exact prefix sizes. Placement intent (x-intended-action) is a belief, never "
        "proof; observed reuse comes from vLLM evidence.",
        "concurrency": concurrency,
        "strategy": "manual",
        "target_endpoint_type": "gateway_chat",
        "system_prefix": prefix,
        "max_tokens": MAX_TOKENS,
        "treatment": treatment,
        "conversations": convs,
    }


def build_scenarios() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, size in SIZES.items():
        conv = lambda tag, a, b: {  # noqa: E731
            "conversation_id_prefix": tag,
            "turns": [_turn(QUESTIONS[0], a), _turn(QUESTIONS[1], b)],
        }
        name = f"e5_local_reuse_{label}"
        out[name] = _scenario(
            name,
            "e5_local_reuse",
            f"A->A: turn 1 populates the ~{label} prefix on worker_a, turn 2 continues on "
            "worker_a (local reuse possible; verify observed reuse).",
            synthetic_prefix(name, size),
            [conv("e5_local", "worker_a", "worker_a")],
        )
        name = f"e5_recompute_control_{label}"
        out[name] = _scenario(
            name,
            "e5_recompute_control",
            f"E5 control, transfer disabled: turn 1 populates the ~{label} prefix on worker_a, "
            "turn 2 is forced to worker_b, which has never seen it and must recompute.",
            synthetic_prefix(name, size),
            [conv("e5_control", "worker_a", "worker_b")],
        )
        name = f"e5_destination_hit_{label}"
        out[name] = _scenario(
            name,
            "e5_destination_hit",
            f"Independent destination hit: the ~{label} prefix is warmed separately on "
            "worker_b, then an A-origin continuation goes to worker_b, which uses its OWN "
            "cache entry. No A->B hop occurred; this is not transfer evidence.",
            synthetic_prefix(name, size),
            [
                {
                    "conversation_id_prefix": "e5_warm_b",
                    "turns": [_turn(WARM_QUESTION, "worker_b")],
                },
                conv("e5_dest", "worker_a", "worker_b"),
            ],
        )
    name = f"e5_local_eviction_{EVICTION_SIZE}"
    fillers = [
        {
            "conversation_id_prefix": f"e5_filler_{i:02d}",
            "system_prefix": synthetic_prefix(f"{name}_filler_{i}", FILLER_TOKENS),
            "turns": [_turn(WARM_QUESTION, "worker_a")],
        }
        for i in range(FILLER_COUNT)
    ]
    out[name] = _scenario(
        name,
        "e5_local_eviction",
        f"A->A after eviction pressure: turn 1 populates the ~{EVICTION_SIZE} prefix on "
        f"worker_a; {FILLER_COUNT} filler conversations with unique ~{FILLER_TOKENS} token "
        f"prefixes are forced onto worker_a while turn 2 waits {EVICTION_DELAY_S:.0f}s. "
        "Whether the prefix was actually evicted is UNVERIFIED by the scenario (filler "
        "volume is an assumption about the KV budget); confirm with vLLM prefix-cache and KV "
        "usage evidence, otherwise report the case as inconclusive, not as a miss.",
        synthetic_prefix(name, SIZES[EVICTION_SIZE]),
        [
            {
                "conversation_id_prefix": "e5_evict",
                "turns": [
                    _turn(QUESTIONS[0], "worker_a"),
                    _turn(QUESTIONS[1], "worker_a", delay_seconds=EVICTION_DELAY_S),
                ],
            },
            *fillers,
        ],
        concurrency=2,
    )
    return out


def main(dest: Path | None = None) -> list[Path]:
    dest = dest or Path(__file__).resolve().parents[4] / "config" / "scenarios"
    written = []
    for name, data in build_scenarios().items():
        path = dest / f"{name}.json"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in main():
        print(p)

"""The application's real globally shared prompt prefix, for E3's headline trace.

The app builds it per request with ``build_query_proposal_partition(prompt, schema)``; the
``schema`` comes from the MCP dataset-schema resource at runtime. This module pins that
schema (nyc-yellow-taxi 2024-01, standard TLC yellow-taxi columns) so the scenario's
``system_prefix`` can be checked for drift against the app's builder instead of being padded.
"""

from __future__ import annotations

from app.prefix import build_query_proposal_partition

CANONICAL_TAXI_SCHEMA = {
    "columns": [
        "VendorID",
        "tpep_pickup_datetime",
        "tpep_dropoff_datetime",
        "passenger_count",
        "trip_distance",
        "RatecodeID",
        "store_and_fwd_flag",
        "PULocationID",
        "DOLocationID",
        "payment_type",
        "fare_amount",
        "extra",
        "mta_tax",
        "tip_amount",
        "tolls_amount",
        "improvement_surcharge",
        "total_amount",
        "congestion_surcharge",
        "Airport_fee",
    ],
    "dataset": "nyc-yellow-taxi",
    "month": "2024-01",
}


def canonical_system_prefix() -> str:
    """Globally shared region of the app's query-proposal prompt (system + rules + schema)."""
    return build_query_proposal_partition("", CANONICAL_TAXI_SCHEMA).global_shared

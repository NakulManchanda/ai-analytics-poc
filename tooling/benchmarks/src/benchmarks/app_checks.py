"""Checks that need the application package (the optional ``app`` extra).

Everything here imports ``app`` lazily so the core replay/benchmark tooling works without it.

The application's real globally shared prompt prefix, for E3's headline trace.

The app builds it per request with ``build_query_proposal_partition(prompt, schema)``; the
``schema`` comes from the MCP dataset-schema resource at runtime. This module pins that
schema (nyc-yellow-taxi 2024-01, standard TLC yellow-taxi columns) so the scenario's
``system_prefix`` can be checked for drift against the app's builder instead of being padded.
"""

from __future__ import annotations

import importlib

from benchmarks.scenarios.models import ScenarioConfig

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


def _require_app(module: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            f"{module} is unavailable: this check needs the application package. "
            "Run with the 'app' extra, e.g. `uv run --project tooling/benchmarks "
            "--extra app ...`."
        ) from exc


def canonical_system_prefix() -> str:
    """Globally shared region of the app's query-proposal prompt (system + rules + schema)."""
    prefix = _require_app("app.prefix")
    return prefix.build_query_proposal_partition(
        "", CANONICAL_TAXI_SCHEMA
    ).global_shared


def validate_catalogue_alignment(config: ScenarioConfig) -> list[str]:
    """Check whether scenario questions align with the query catalogue.

    Returns a list of warnings for questions that miss the catalogue.
    """
    lookup = _require_app("app.query_catalogue").lookup
    warnings: list[str] = []
    for conv_idx, conv in enumerate(config.conversations):
        for turn_idx, turn in enumerate(conv.turns):
            hit = lookup(turn.question)
            if hit is None:
                warnings.append(
                    f"Conv {conv_idx + 1} Turn {turn_idx + 1} misses query catalogue: "
                    f"'{turn.question[:50]}...'"
                )
            elif turn.expected_tool and hit[0] != turn.expected_tool:
                warnings.append(
                    f"Conv {conv_idx + 1} Turn {turn_idx + 1} tool mismatch: "
                    f"expected '{turn.expected_tool}', catalogue has '{hit[0]}'"
                )
    return warnings

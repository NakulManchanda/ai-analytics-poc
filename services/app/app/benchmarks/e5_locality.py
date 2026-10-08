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
  This is the E5 control the real-transfer treatment (#133, not implemented) will be compared
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
_SENTENCES = (
    "The analytics assistant answers questions about taxi trips using governed read only tools.",
    "Every answer must cite the observation returned by a tool and never invent figures.",
    "Trip volume is grouped by pickup zone, hour of day, weekday and payment type.",
    "Fares, tips, tolls and surcharges are reported in US dollars per trip.",
    "Distances are reported in miles and durations in minutes between pickup and dropoff.",
    "When a question is ambiguous the assistant states its assumption before answering.",
    "Row limits bound every query so that responses stay small and cheap to read.",
    "Airport trips carry a separate fee and are compared against ordinary city trips.",
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
    text so different cases never share a KV block chain."""
    want = target_tokens * 4 - _SYSTEM_WRAP
    out = f"SYNTHETIC E5 PREFIX case {tag} size {target_tokens}. "
    n = 0
    while len(out) < want:
        out += f"Note {n}. {_SENTENCES[n % len(_SENTENCES)]} "
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
        + " SYNTHETIC prefix-size treatment, not the taxi-agent prefix. Needs the gateway with "
        "ALLOW_FORCED_PLACEMENT=1 and no KV transfer backend. Real-transfer (#133) treatment "
        "is NOT implemented; it must reuse these exact prefix sizes. Placement intent "
        "(x-intended-action) is a belief, never proof; observed reuse comes from vLLM evidence.",
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

"""Prefix Token Contract module for the taxi analytics application.

Differentiates prompt regions according to Section 5 of docs/inference-project-plan.md:
1. Globally shared: system prompt, dataset rules/instructions, tool and MCP schemas.
2. Conversation-shared: prior turns, previous tool calls, DuckDB observations.
3. Unique suffix: newest user/tool suffix, latest question, generation target.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from app.prompts import (
    ANSWER,
    ANSWER_WITH_SCHEMA,
    ASK,
    DATASET_PROFILE,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TAXI_RULES,
    PLANNER,
    PromptAttribution,
    PromptBundle,
)


class PromptRegion(StrEnum):
    GLOBAL_SHARED = "global_shared"
    CONVERSATION_SHARED = "conversation_shared"
    UNIQUE_SUFFIX = "unique_suffix"


@dataclass(frozen=True)
class PrefixPartition:
    """Explicit token breakdown of a prompt across reuse boundaries."""

    global_shared: str
    conversation_shared: str
    unique_suffix: str
    prefix_id: str
    estimated_total_tokens: int
    estimated_shared_tokens: int
    # Which versioned template rendered `global_shared`, with a hash of the exact text.
    attribution: PromptAttribution | None = None

    @property
    def full_prompt(self) -> str:
        parts = [
            p
            for p in (self.global_shared, self.conversation_shared, self.unique_suffix)
            if p
        ]
        return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Deterministic token estimation using 1.33x word count with minimum bound."""
    if not text:
        return 0
    words = text.split()
    if not words:
        return 0
    return max(1, int(len(words) * 1.33))


def compute_prefix_id(global_shared: str, conversation_shared: str) -> str:
    """Compute deterministic 16-character hex hash identifying the reusable prefix."""
    shared_text = f"{global_shared.strip()}\n{conversation_shared.strip()}".strip()
    if not shared_text:
        return "prefix-empty"
    return hashlib.sha256(shared_text.encode("utf-8")).hexdigest()[:16]


def create_prefix_partition(
    *,
    global_shared: str = "",
    conversation_shared: str = "",
    unique_suffix: str = "",
    bundle: PromptBundle | None = None,
) -> PrefixPartition:
    """Create a PrefixPartition calculating stable prefix_id and token estimates.

    ``bundle`` is the versioned template that rendered ``global_shared``; the partition
    then carries the per-call attribution (id, version, SHA-256 of the exact text)."""
    clean_global = global_shared.strip()
    clean_conv = conversation_shared.strip()
    clean_suffix = unique_suffix.strip()

    shared_combined = f"{clean_global}\n{clean_conv}".strip()
    prefix_id = compute_prefix_id(clean_global, clean_conv)

    shared_tokens = estimate_tokens(shared_combined)
    total_tokens = estimate_tokens(f"{shared_combined}\n{clean_suffix}".strip())

    return PrefixPartition(
        global_shared=clean_global,
        conversation_shared=clean_conv,
        unique_suffix=clean_suffix,
        prefix_id=prefix_id,
        estimated_total_tokens=total_tokens,
        estimated_shared_tokens=shared_tokens,
        attribution=bundle.attribute(clean_global) if bundle is not None else None,
    )


def _past_turns(
    bundle: PromptBundle, prior_turns: Sequence[Mapping[str, Any]] | None
) -> list[str]:
    return [
        bundle.render("past_turn", turn_json=json.dumps(turn, sort_keys=True))
        for turn in prior_turns or []
    ]


def build_ask_partition(
    prompt: str,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    prior_messages: Sequence[Mapping[str, str]] | None = None,
) -> PrefixPartition:
    """Build partition for a direct ask query."""
    conv_parts = []
    if prior_messages:
        for msg in prior_messages:
            conv_parts.append(
                ASK.render(
                    "history",
                    role=msg.get("role", "user").upper(),
                    content=msg.get("content", ""),
                )
            )

    return create_prefix_partition(
        global_shared=ASK.render("global", system_prompt=system_prompt),
        conversation_shared="\n".join(conv_parts),
        unique_suffix=ASK.render("user", prompt=prompt),
        bundle=ASK,
    )


def build_query_proposal_partition(
    prompt: str,
    schema: Mapping[str, Any],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    prior_turns: Sequence[Mapping[str, Any]] | None = None,
) -> PrefixPartition:
    """Build partition for proposing a governed taxi query."""
    schema_json = json.dumps(schema, separators=(",", ":"), sort_keys=True)
    return create_prefix_partition(
        global_shared=PLANNER.render(
            "global",
            system_prompt=system_prompt,
            taxi_rules=DEFAULT_TAXI_RULES,
            schema_json=schema_json,
        ),
        conversation_shared="\n".join(_past_turns(PLANNER, prior_turns)),
        unique_suffix=PLANNER.render("user", question=prompt),
        bundle=PLANNER,
    )


def build_query_answer_partition(
    prompt: str,
    query_result: Mapping[str, Any],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    schema: Mapping[str, Any] | None = None,
    prior_turns: Sequence[Mapping[str, Any]] | None = None,
) -> PrefixPartition:
    """Build partition for generating an answer from DuckDB query results."""
    if schema:
        bundle = ANSWER_WITH_SCHEMA
        global_shared = bundle.render(
            "global",
            system_prompt=system_prompt,
            taxi_rules=DEFAULT_TAXI_RULES,
            schema_json=json.dumps(schema, sort_keys=True),
        )
    else:
        bundle = ANSWER
        global_shared = bundle.render(
            "global", system_prompt=system_prompt, taxi_rules=DEFAULT_TAXI_RULES
        )

    conv_parts = _past_turns(bundle, prior_turns)
    # Tool observation belongs to conversation context, never to instructions.
    conv_parts.append(
        bundle.render(
            "observation",
            result_json=json.dumps(query_result, separators=(",", ":"), sort_keys=True),
        )
    )
    return create_prefix_partition(
        global_shared=global_shared,
        conversation_shared="\n".join(conv_parts),
        unique_suffix=bundle.render("user", question=prompt),
        bundle=bundle,
    )


@dataclass(frozen=True)
class ExactTokenCounts:
    """Exact per-region token counts from vLLM's `/tokenize` endpoint (D17).

    Never estimated: when not in serve mode, or the call fails/times out for
    any reason, all three counts stay `None` and `token_count_unavailable_reason`
    is set. This is a distinct field from `estimate_tokens()`'s word-count
    heuristic, which keeps its existing behavior for its existing callers.
    """

    global_shared_tokens: int | None = None
    conversation_shared_tokens: int | None = None
    unique_suffix_tokens: int | None = None
    token_count_unavailable_reason: str | None = None


def _derive_tokenize_url(gateway_url: str) -> str:
    """Sibling `/tokenize` route for a serve-mode gateway URL such as
    `http://host:port/serve` or `http://host:port/v1/chat/completions`."""
    base = gateway_url.rstrip("/")
    for suffix in ("/v1/chat/completions", "/serve"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/tokenize"


def count_prefix_tokens_exact(
    partition: PrefixPartition,
    *,
    model_id: str,
    gateway_url: str | None,
    is_serve_mode: bool = True,
    http_client: Any | None = None,
    timeout_seconds: float = 5.0,
) -> ExactTokenCounts:
    """Exact global/conversation/unique token counts via vLLM's OpenAI-compatible
    `/tokenize` endpoint, reached through the owned inference gateway (D17).

    Serve mode only, and never a fallback to `estimate_tokens()`: any failure
    (not serve mode, no gateway URL, connection error, timeout, non-200, or a
    malformed response) returns all-`None` counts with a reason instead.
    """
    if not is_serve_mode or not gateway_url:
        return ExactTokenCounts(token_count_unavailable_reason="not_serve_mode")

    tokenize_url = _derive_tokenize_url(gateway_url)
    client = http_client
    owns_client = False
    if client is None:
        client = httpx.Client(timeout=timeout_seconds)
        owns_client = True
    try:
        counts: dict[str, int] = {}
        regions = (
            ("global_shared_tokens", partition.global_shared),
            ("conversation_shared_tokens", partition.conversation_shared),
            ("unique_suffix_tokens", partition.unique_suffix),
        )
        for field_name, text in regions:
            if not text:
                counts[field_name] = 0
                continue
            try:
                response = client.post(
                    tokenize_url, json={"model": model_id, "prompt": text}
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                return ExactTokenCounts(
                    token_count_unavailable_reason=f"tokenize_call_failed: {exc}"
                )
            except httpx.RequestError as exc:
                return ExactTokenCounts(
                    token_count_unavailable_reason=f"tokenize_call_failed: {exc}"
                )
            if response.status_code != 200:
                return ExactTokenCounts(
                    token_count_unavailable_reason=(
                        f"tokenize_http_{response.status_code}"
                    )
                )
            try:
                data = response.json()
            except ValueError:
                return ExactTokenCounts(
                    token_count_unavailable_reason="tokenize_response_malformed"
                )
            count = data.get("count")
            tokens = data.get("tokens")
            if isinstance(count, int) and not isinstance(count, bool):
                counts[field_name] = count
            elif isinstance(tokens, list):
                counts[field_name] = len(tokens)
            else:
                return ExactTokenCounts(
                    token_count_unavailable_reason="tokenize_response_malformed"
                )
        return ExactTokenCounts(
            global_shared_tokens=counts["global_shared_tokens"],
            conversation_shared_tokens=counts["conversation_shared_tokens"],
            unique_suffix_tokens=counts["unique_suffix_tokens"],
        )
    finally:
        if owns_client:
            client.close()


def build_dataset_profile_partition(
    prompt: str,
    dataset_profile: Mapping[str, Any],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> PrefixPartition:
    """Build partition for answering with a dataset profile."""
    profile_json = json.dumps(dataset_profile, separators=(",", ":"), sort_keys=True)
    return create_prefix_partition(
        global_shared=DATASET_PROFILE.render("global", system_prompt=system_prompt),
        conversation_shared=DATASET_PROFILE.render(
            "observation", profile_json=profile_json
        ),
        unique_suffix=DATASET_PROFILE.render("user", question=prompt),
        bundle=DATASET_PROFILE,
    )
